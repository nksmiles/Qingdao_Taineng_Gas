"""每日用量历史持久化（v1.4.3 新增）。

ESLink 接口的 ``fee/chart/iotBarChart`` 只返回约 37 天的滚动窗口，
超过该窗口的「每日耗气量 YYYY-MM-DD」实体在 HA 重启 / 升级 / 重载后
会因新一次 ``async_setup_entry`` 拿不到对应日期而变成 ``unavailable``
（实体仍在 entity_registry 里，但本集成没把它加进运行态），
并连带把同名仪表盘卡片从 8 月 1 号起全部拉成不可用。

本模块用 ``homeassistant.helpers.storage.Store`` 把已结算的每日用量
按 ``{date: volume}`` 的形式落到 ``.storage/qingdao_taineng_gas/<entry_id>``
JSON 文件中；下次加载集成时先读历史、再与本次 API 返回合并，既能
恢复已经滑出滚动窗口的 8 月份实体，也避免与已确认的同一天冲突。

为防止历史无限膨胀，最多保留 ``MAX_DAILY_HISTORY_DAYS`` 天（默认 400，
约 13 个月，足以覆盖一整个计费账期 + 跨年）。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

# 存储键版本：JSON 结构变化时手动 +1，避免老文件被误读
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "qingdao_taineng_gas.daily_history"

# 单个 entry 最多保留的历史天数（约 13 个月；2 个账期 + 跨年余量）
MAX_DAILY_HISTORY_DAYS = 400


def _entry_store_key(entry_id: str) -> str:
    """按 entry_id 拼出本集成专属的存储文件名。"""
    return f"{STORAGE_KEY_PREFIX}.{entry_id}"


def get_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """拿到本 entry 专属的 Store（每次调用都返回新实例，HA 内部有锁）。"""
    return Store[dict[str, Any]](hass, STORAGE_VERSION, _entry_store_key(entry_id))


async def async_load_history(
    hass: HomeAssistant, entry_id: str
) -> dict[str, float]:
    """读取历史；不存在时返回空 dict。

    返回的 dict 形如 ``{"2026-08-15": 0.4, ...}``，按日期升序遍历可得到
    ``[{date, volume}, ...]``（由 sensor.py 自行转换）。
    """
    store = get_store(hass, entry_id)
    data = await store.async_load()
    if not isinstance(data, dict):
        return {}
    raw = data.get("daily")
    if not isinstance(raw, dict):
        return {}
    history: dict[str, float] = {}
    for date_str, volume in raw.items():
        if not isinstance(date_str, str) or len(date_str) != 10:
            continue
        try:
            history[date_str] = round(float(volume), 4)
        except (TypeError, ValueError):
            continue
    return history


async def async_save_history(
    hass: HomeAssistant,
    entry_id: str,
    daily: dict[str, float],
) -> None:
    """把 ``{date: volume}`` 持久化，保留最近 N 天。

    - 已存在的日期会被新值覆盖（正常情况下不会改，但接口偶尔会回填修正值）；
    - 早于 ``today - MAX_DAILY_HISTORY_DAYS`` 的历史会被裁掉，避免文件无界增长。
    """
    today = date.today()
    cutoff = today - timedelta(days=MAX_DAILY_HISTORY_DAYS)
    cleaned: dict[str, float] = {}
    for date_str, volume in daily.items():
        try:
            d = date.fromisoformat(date_str)
        except ValueError:
            continue
        if d < cutoff:
            continue
        cleaned[date_str] = round(float(volume), 4)

    store = get_store(hass, entry_id)
    await store.async_save({"daily": cleaned})
    _LOGGER.debug(
        "已写入每日用量历史：%d 天（entry=%s，截止 %s）",
        len(cleaned),
        entry_id,
        cutoff.isoformat(),
    )


def merge_with_history(
    api_rows: list[dict[str, Any]],
    history: dict[str, float],
) -> list[dict[str, Any]]:
    """把 API 返回的当日明细与历史合并（API 优先；按 date 升序去重）。

    ``api_rows`` 形如 ``[{date: '2026-08-15', volume: 0.4}, ...]``。
    """
    merged: dict[str, float] = {}
    for item in api_rows:
        d = str(item.get("date") or "")
        if len(d) != 10:
            continue
        try:
            merged[d] = round(float(item.get("volume") or 0.0), 4)
        except (TypeError, ValueError):
            continue
    for d, v in history.items():
        merged.setdefault(d, v)
    return [
        {"date": d, "volume": v}
        for d, v in sorted(merged.items(), key=lambda kv: kv[0])
    ]


def to_recent_days(merged: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """取合并后列表的末尾 ``limit`` 天，供 ``recent_days`` 属性使用。"""
    return [{"date": item["date"], "volume": round(float(item["volume"]), 4)}
            for item in merged[-limit:]]
