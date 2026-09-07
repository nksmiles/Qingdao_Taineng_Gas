"""传感器实体定义。

传感器与泰能燃气“非自然月计费”口径对应关系：
  * 官方读数在每月末抄表日结算 → 「上个账期累计表读数」= 官方读数（抄表日更新）
  * 抄表日之后逐日结算的用量之和 → 「本账期累计用量」
  * 官方读数 + 本账期累计用量 = 「预计当前读数」（连续估算值，以属性提供）
  * 每个已结算日生成一个「每日耗气量」实体，逐日补录，值固定不变。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_BASE_READING,
    ATTR_CYCLE_END,
    ATTR_CYCLE_START,
    ATTR_DATA_LAG,
    ATTR_ESTIMATED_READING,
    ATTR_METER_NO,
    ATTR_METER_READING_DATE,
    ATTR_READING_DATE,
    ATTR_RECENT_DAYS,
    ATTR_SETTLED_DATE,
    ATTR_USER_NO,
    DOMAIN,
    KEY_CYCLE_USAGE,
    KEY_DAILY_SERIES,
    KEY_DAILY_USAGE,
    KEY_METER_READING,
    MANUFACTURER,
)
from .coordinator import TanengGasCoordinator, build_last_reset

_LOGGER = logging.getLogger(__name__)

UNIT = UnitOfVolume.CUBIC_METERS  # m³


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置传感器平台。"""
    coordinator: TanengGasCoordinator = hass.data[DOMAIN][entry.entry_id]

    # 1) 核心实体（口径随账期，见类注释）
    core = [
        MeterReadingSensor(coordinator, entry),
        CycleUsageSensor(coordinator, entry),
        DailyUsageSensor(coordinator, entry),
    ]

    # 2) 每日耗气量实体：为窗口内每个已结算日生成一个，新结算日出现后再补
    added_dates: set[str] = set()

    def _dates_in_data() -> list[dict[str, Any]]:
        """从协调器数据取窗口内全部已结算日（date→volume 列表）。"""
        return coordinator.data.get("daily_series", []) if coordinator.data else []

    def _fresh_daily_entities() -> list[DailyHistorySensor]:
        fresh = [
            item
            for item in _dates_in_data()
            if item.get("date") not in added_dates
        ]
        added_dates.update(item["date"] for item in fresh)
        return [DailyHistorySensor(coordinator, entry, item) for item in fresh]

    async_add_entities([*core, *_fresh_daily_entities()])

    # 每次协调器更新成功后有新的已结算日时，追加对应的每日耗气量实体
    @callback
    def _on_update() -> None:
        entities = _fresh_daily_entities()
        if entities:
            _LOGGER.debug("追加每日耗气量实体：%s", [e.name for e in entities])
            async_add_entities(entities)

    coordinator.async_add_listener(_on_update)


def _device_info(coordinator: TanengGasCoordinator, entry: ConfigEntry) -> dict[str, Any]:
    """设备信息：与核心传感器挂到同一设备。"""
    meter_no = coordinator.user_info.get("meter_no") or entry.entry_id
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": f"泰能燃气 {meter_no[-4:]}",
        "manufacturer": MANUFACTURER,
        "model": "NB-IoT 物联网燃气表",
        "configuration_url": "https://cloudselfhelp-mobile.eslink.cc/",
    }


class TanengGasBaseEntity(CoordinatorEntity[TanengGasCoordinator], SensorEntity):
    """核心传感器基类。"""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UNIT
    _attr_device_class = SensorDeviceClass.GAS

    def __init__(self, coordinator: TanengGasCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def device_info(self) -> dict[str, Any]:
        """设备信息。"""
        return _device_info(self.coordinator, self._entry)

    @property
    def _data(self) -> dict[str, Any]:
        return self.coordinator.data or {}

    def _common_attributes(self) -> dict[str, Any]:
        return {
            ATTR_USER_NO: self.coordinator.masked_user_no(),
            ATTR_METER_NO: self.coordinator.masked_meter_no(),
        }

    @property
    def available(self) -> bool:
        """更新失败时保留上一次的值，避免图表出现断线。"""
        return self.coordinator.last_update_success and self.coordinator.data is not None


class MeterReadingSensor(TanengGasBaseEntity):
    """上个账期累计表读数。

    泰能每月在抄表日结算一次，官方读数（meterReading）即上个账期结束时的
    累计表读数，仅在每次抄表后更新。实时连续估算值见 estimated_reading 属性。
    """

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: TanengGasCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, KEY_METER_READING)
        self._attr_name = "上个账期累计表读数"

    @property
    def native_value(self) -> float | None:
        value = self._data.get("official_reading")
        return None if value is None else round(float(value), 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = self._common_attributes()
        attrs.update(
            {
                ATTR_BASE_READING: self._data.get("official_reading"),
                ATTR_METER_READING_DATE: self._data.get("base_date"),
                ATTR_ESTIMATED_READING: self._data.get("estimated_reading"),
                ATTR_SETTLED_DATE: self._data.get("settled_date"),
                ATTR_DATA_LAG: self._data.get("data_lag_days"),
            }
        )
        return attrs


class CycleUsageSensor(TanengGasBaseEntity):
    """本账期累计用量。

    泰能计费周期非自然月：每月末抄表（如 2026-08-30 抄表，读数 207.0），
    上一个账期即告结算，其后每日新结算的用量逐日累加为「本账期累计用量」，
    直到下一次抄表时重新起算。是否计入抄表当天的用量由 include_base_day 开关控制。
    """

    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:calendar-month"

    def __init__(self, coordinator: TanengGasCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, KEY_CYCLE_USAGE)
        self._attr_name = "本账期累计用量"

    @property
    def native_value(self) -> float | None:
        value = self._data.get("cycle_total")
        return None if value is None else round(float(value), 3)

    @property
    def last_reset(self) -> datetime | None:
        """以账期起始日（官方抄表日）零点为重置点。"""
        return build_last_reset(self._data.get("cycle_start"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = self._common_attributes()
        attrs.update(
            {
                ATTR_CYCLE_START: self._data.get("cycle_start"),
                ATTR_CYCLE_END: self._data.get("cycle_end"),
                ATTR_SETTLED_DATE: self._data.get("settled_date"),
                ATTR_DATA_LAG: self._data.get("data_lag_days"),
            }
        )
        return attrs


class DailyUsageSensor(TanengGasBaseEntity):
    """最近一日用量（最后已结算日的用量）。"""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:fire"

    def __init__(self, coordinator: TanengGasCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, KEY_DAILY_USAGE)
        self._attr_name = "最近一日用量"

    @property
    def native_value(self) -> float | None:
        value = self._data.get("daily_usage")
        return None if value is None else round(float(value), 3)

    @property
    def last_reset(self) -> datetime | None:
        return build_last_reset(self._data.get("settled_date"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = self._common_attributes()
        attrs.update(
            {
                ATTR_READING_DATE: self._data.get("settled_date"),
                ATTR_RECENT_DAYS: self._data.get("recent_days", []),
                ATTR_DATA_LAG: self._data.get("data_lag_days"),
            }
        )
        return attrs


class DailyHistorySensor(SensorEntity):
    """每日耗气量（单个已结算日的用量，值固定）。

    为接口滚动窗口内的每个已结算日生成一个实体（名称含日期），
    某一天结算完成即锁定该值，不会再变化，可直接用于逐日核对与画图。
    """

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UNIT
    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:chart-bar"

    def __init__(
        self,
        coordinator: TanengGasCoordinator,
        entry: ConfigEntry,
        item: dict[str, Any],
    ) -> None:
        super().__init__()
        self._coordinator = coordinator
        self._date = str(item["date"])
        self._attr_unique_id = f"{entry.entry_id}_{KEY_DAILY_SERIES}{self._date}"
        self._attr_name = f"每日耗气量 {self._date}"
        self._attr_native_value = round(float(item["volume"]), 3)

    @property
    def device_info(self) -> dict[str, Any]:
        return _device_info(self._coordinator, self._entry)

    @property
    def _entry(self) -> ConfigEntry:
        return self._coordinator.entry

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            ATTR_READING_DATE: self._date,
            ATTR_USER_NO: self._coordinator.masked_user_no(),
            ATTR_METER_NO: self._coordinator.masked_meter_no(),
        }
