"""传感器实体定义。

传感器与泰能燃气计费口径对应关系：
  * 官方读数在每月末抄表日结算 → 「上个账期累计表读数」= 官方读数（抄表日更新）
  * 抄表日之后逐日结算的用量之和 → 「本账期累计用量」
  * 官方读数 + 本账期累计用量 = 「预计当前读数」，即「燃气总用量」实体值
    （total_increasing，可直接接入能源面板做逐日/逐月统计）
  * 「当年累计用量」= 官方年度已结算累计（cycleCreditQty）+ 本年度未结算增量，
    户·年累计、每年 1 月 1 日清零，用于核算阶梯计费（阈值 228 / 348 m³）
  * 「燃气费单价」= 按当年累计用量判定的当前边际单价（3.54 / 4.12 / 4.99 元/m³），
    跨过阈值后自动切换到更高一档
  * 每个已结算日生成一个「每日耗气量」实体，逐日补录，值固定不变。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
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
    ATTR_ANNUAL_CYCLE_END,
    ATTR_ANNUAL_SETTLED,
    ATTR_ANNUAL_UNBILLED,
    ATTR_BASE_READING,
    ATTR_CYCLE_END,
    ATTR_CYCLE_START,
    ATTR_DATA_LAG,
    ATTR_ESTIMATED_READING,
    ATTR_LADDER,
    ATTR_METER_NO,
    ATTR_METER_READING_DATE,
    ATTR_MONTHLY,
    ATTR_NEXT_TIER_AT,
    ATTR_NEXT_TIER_REMAINING,
    ATTR_READING_DATE,
    ATTR_RECENT_DAYS,
    ATTR_SETTLED_DATE,
    ATTR_TIER,
    ATTR_TIER_NAME,
    ATTR_TIER_PRICES,
    ATTR_TIER_REMAINING,
    ATTR_TIER_THRESHOLDS,
    ATTR_USER_NO,
    DOMAIN,
    GAS_TIER_NAMES,
    GAS_TIER_PRICES,
    GAS_TIER_THRESHOLDS,
    KEY_ANNUAL_USAGE,
    KEY_CYCLE_USAGE,
    KEY_DAILY_SERIES,
    KEY_DAILY_USAGE,
    KEY_GAS_PRICE,
    KEY_METER_READING,
    KEY_TOTAL_GAS,
    MANUFACTURER,
    gas_price_for_annual_usage,
    tier_for_annual_usage,
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

    # 1) 核心实体（口径见类注释）
    core = [
        MeterReadingSensor(coordinator, entry),
        CycleUsageSensor(coordinator, entry),
        TotalGasSensor(coordinator, entry),
        AnnualUsageSensor(coordinator, entry),
        GasPriceSensor(coordinator, entry),
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


def _annual_tier_attributes(data: dict[str, Any]) -> dict[str, Any]:
    """年度阶梯计费上下文（「当年累计用量」与「燃气费单价」共用）。"""
    usage = data.get("annual_usage")
    tier = tier_for_annual_usage(usage)
    attrs: dict[str, Any] = {
        ATTR_ANNUAL_SETTLED: data.get("annual_settled"),
        ATTR_ANNUAL_UNBILLED: data.get("annual_unbilled"),
        ATTR_ANNUAL_CYCLE_END: data.get("annual_cycle_end"),
        ATTR_LADDER: data.get("annual_ladder"),
        ATTR_TIER_THRESHOLDS: GAS_TIER_THRESHOLDS,
        ATTR_TIER_PRICES: GAS_TIER_PRICES,
        ATTR_TIER_REMAINING: data.get("tier_remaining", []),
        ATTR_TIER: tier,
        ATTR_TIER_NAME: GAS_TIER_NAMES[tier - 1] if tier else None,
        ATTR_NEXT_TIER_AT: None,
        ATTR_NEXT_TIER_REMAINING: None,
    }
    if tier is not None and usage is not None and tier < len(GAS_TIER_THRESHOLDS):
        next_at = GAS_TIER_THRESHOLDS[tier - 1]
        attrs[ATTR_NEXT_TIER_AT] = next_at
        attrs[ATTR_NEXT_TIER_REMAINING] = round(max(0.0, next_at - usage), 4)
    return attrs


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


class TotalGasSensor(TanengGasBaseEntity):
    """燃气总用量（预计当前读数，燃气表累计读数）。

    值 = 上个账期累计表读数（官方读数）+ 本账期累计用量。
    total_increasing、数值单调递增（抄表结算、跨年都不回落），
    可直接接入能源面板「能耗 → 天然气」做逐日 / 逐月统计。
    """

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:gauge"

    def __init__(self, coordinator: TanengGasCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, KEY_TOTAL_GAS)
        self._attr_name = "燃气总用量"

    @property
    def native_value(self) -> float | None:
        value = self._data.get("estimated_reading")
        return None if value is None else round(float(value), 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = self._common_attributes()
        attrs.update(
            {
                ATTR_BASE_READING: self._data.get("official_reading"),
                ATTR_METER_READING_DATE: self._data.get("base_date"),
                ATTR_CYCLE_START: self._data.get("cycle_start"),
                ATTR_SETTLED_DATE: self._data.get("settled_date"),
                ATTR_DATA_LAG: self._data.get("data_lag_days"),
                ATTR_ESTIMATED_READING: self._data.get("estimated_reading"),
            }
        )
        return attrs


class AnnualUsageSensor(TanengGasBaseEntity):
    """当年累计用量（户·年累计，每年 1 月 1 日清零）。

    值 = 官方年度已结算累计（cycleCreditQty，每月抄表结算后更新）
       + 本年度内抄表后仍未结算的用量增量（随每天结算逐日递增）。
    该累计量用于泰能阶梯计费核算：跨过 228 / 348 m³ 阈值后，
    后续每立方米按更高一档单价计费（详见「燃气费单价」实体）。
    """

    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:calendar-range"

    def __init__(self, coordinator: TanengGasCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, KEY_ANNUAL_USAGE)
        self._attr_name = "当年累计用量"

    @property
    def native_value(self) -> float | None:
        value = self._data.get("annual_usage")
        return None if value is None else round(float(value), 3)

    @property
    def last_reset(self) -> datetime | None:
        """每年 1 月 1 日零点为重置点。"""
        year = self._data.get("annual_cycle_year")
        if not year:
            return None
        try:
            return datetime.combine(date(int(year), 1, 1), datetime.min.time())
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = self._common_attributes()
        attrs.update(_annual_tier_attributes(self._data))
        # 当年逐月用量：[{month: '2026-08', volume: 12.3}, ...]，
        # 供卡片画「当年已出账单各月趋势」柱状图
        attrs[ATTR_MONTHLY] = self._data.get("monthly_series", [])
        return attrs


class GasPriceSensor(TanengGasBaseEntity):
    """燃气费单价（当前边际单价，元/m³）。

    按「当年累计用量」判定当前阶梯，状态 = 下一立方米将适用的单价：
    第一阶梯 3.54 / 第二阶梯 4.12 / 第三阶梯 4.99（元/m³）。
    当年累计跨过 228 / 348 阈值后，本实体自动切换到更高一档单价。
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "CNY/m³"
    _attr_device_class = None  # 单价不是金额本身，不使用 monetary 类
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash-multiple"

    def __init__(self, coordinator: TanengGasCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, KEY_GAS_PRICE)
        self._attr_name = "燃气费单价"

    @property
    def native_value(self) -> float | None:
        return gas_price_for_annual_usage(self._data.get("annual_usage"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = self._common_attributes()
        attrs.update(_annual_tier_attributes(self._data))
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

    注意：不能为 device_class=gas 的实体设置 state_class=measurement
    （HA 只允许 None / total / total_increasing）。本实体的值是某一天的
    耗气量快照，创建后即固定、不随时间变化，既非测量值也非累计量，
    因此不设 state_class（保持默认 None），仅保留 gas 分类用于展示。
    """

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UNIT
    _attr_device_class = SensorDeviceClass.GAS
    # 刻意不设置 _attr_state_class（保持 None）：
    # gas device_class 不允许 measurement，详见类注释。
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
