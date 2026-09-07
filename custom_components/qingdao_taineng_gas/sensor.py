"""传感器实体定义。"""
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
    ATTR_DATA_LAG,
    ATTR_METER_NO,
    ATTR_METER_READING_DATE,
    ATTR_READING_DATE,
    ATTR_RECENT_DAYS,
    ATTR_SETTLED_DATE,
    ATTR_USER_NO,
    DOMAIN,
    KEY_DAILY_USAGE,
    KEY_METER_READING,
    KEY_MONTHLY_USAGE,
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
    async_add_entities(
        [
            MeterReadingSensor(coordinator, entry),
            DailyUsageSensor(coordinator, entry),
            MonthlyUsageSensor(coordinator, entry),
        ]
    )


class TanengGasBaseEntity(CoordinatorEntity[TanengGasCoordinator], SensorEntity):
    """传感器基类。"""

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
        meter_no = self.coordinator.user_info.get("meter_no") or self._entry.entry_id
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": f"泰能燃气 {meter_no[-4:]}",
            "manufacturer": MANUFACTURER,
            "model": "NB-IoT 物联网燃气表",
            "configuration_url": "https://cloudselfhelp-mobile.eslink.cc/",
        }

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
    """燃气表累计读数（估算值）。

    官方读数 + 抄表日之后的已结算用量之和，保证曲线连续。
    """

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: TanengGasCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, KEY_METER_READING)
        self._attr_name = "累计表读数"

    @property
    def native_value(self) -> float | None:
        value = self._data.get("cumulative_reading")
        return None if value is None else round(float(value), 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = self._common_attributes()
        attrs.update(
            {
                ATTR_BASE_READING: self._data.get("base_reading"),
                ATTR_METER_READING_DATE: self._data.get("base_date"),
                ATTR_SETTLED_DATE: self._data.get("settled_date"),
                ATTR_DATA_LAG: self._data.get("data_lag_days"),
            }
        )
        return attrs


class DailyUsageSensor(TanengGasBaseEntity):
    """最近一日已结算用量。"""

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


class MonthlyUsageSensor(TanengGasBaseEntity):
    """本月累计用量（仅含已结算日）。"""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:calendar-month"

    def __init__(self, coordinator: TanengGasCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, KEY_MONTHLY_USAGE)
        self._attr_name = "本月累计用量"

    @property
    def native_value(self) -> float | None:
        value = self._data.get("month_total")
        return None if value is None else round(float(value), 3)

    @property
    def last_reset(self) -> datetime | None:
        """以每月 1 日零点为重置点。"""
        settled = self._data.get("settled_date")
        if not settled:
            return None
        try:
            base = datetime.fromisoformat(settled)
        except ValueError:
            return None
        return datetime(base.year, base.month, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = self._common_attributes()
        attrs.update(
            {
                ATTR_SETTLED_DATE: self._data.get("settled_date"),
                ATTR_RECENT_DAYS: self._data.get("recent_days", []),
            }
        )
        return attrs
