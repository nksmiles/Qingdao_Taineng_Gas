# -*- coding: utf-8 -*-
"""
v1.4.3 修复验证：8 月份「每日耗气量」实体在 HA 重启后应能恢复，不再 unavailable。

ESLink 接口 ``fee/chart/iotBarChart`` 的 type=month 返回约 37 天滚动窗口；
10 月份再调用时，8 月份数据已落出窗口，单纯靠 API 恢复会缺 8 月实体，
对应的仪表盘卡片会变成 ``unavailable``。

本脚本不依赖 HA，直接走 ``store.py`` 的 ``async_load_history`` /
``async_save_history`` / ``merge_with_history`` 与协调器 ``_compute``，
验证「先用一次 API + 历史落盘，再模拟「今天=10-12」仅返回 9 月以后窗口」
时，合并后的 ``daily_series`` 仍能拿到 8 月的每日数据。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = "qingdao_taineng_gas"
PKG_DIR = ROOT / "custom_components" / PKG

# Windows 控制台默认编码（GBK）无法输出 m³ 等字符
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


# ---------------------------------------------------------------
# 1. 注入假 HA 模块（与 test_cumulative_reading.py 保持一致）
# ---------------------------------------------------------------
def _fake_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


for _name in [
    "homeassistant",
    "homeassistant.config_entries",
    "homeassistant.core",
    "homeassistant.exceptions",
    "homeassistant.helpers",
    "homeassistant.helpers.aiohttp_client",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.update_coordinator",
]:
    _fake_module(_name)

_ha = sys.modules["homeassistant"]
_ha.config_entries = sys.modules["homeassistant.config_entries"]
_ha.core = sys.modules["homeassistant.core"]
_ha.exceptions = sys.modules["homeassistant.exceptions"]
_ha.helpers = sys.modules["homeassistant.helpers"]
_ha.helpers.aiohttp_client = sys.modules["homeassistant.helpers.aiohttp_client"]
_ha.helpers.storage = sys.modules["homeassistant.helpers.storage"]
_ha.helpers.update_coordinator = sys.modules["homeassistant.helpers.update_coordinator"]


class _ConfigEntry:
    pass


class _HomeAssistant:
    pass


class _ConfigEntryAuthFailed(Exception):
    pass


class _UpdateFailed(Exception):
    pass


class _DataUpdateCoordinator:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, name=None, update_interval=None):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data = None
        self.last_update_success = True


_ha.config_entries.ConfigEntry = _ConfigEntry
_ha.core.HomeAssistant = _HomeAssistant
_ha.exceptions.ConfigEntryAuthFailed = _ConfigEntryAuthFailed
_ha.helpers.aiohttp_client.async_get_clientsession = lambda hass: None
_ha.helpers.update_coordinator.DataUpdateCoordinator = _DataUpdateCoordinator
_ha.helpers.update_coordinator.UpdateFailed = _UpdateFailed


# ---------------------------------------------------------------
# 2. 内存版 Store（不落盘，仅让 store 模块可被加载与测试）
# ---------------------------------------------------------------
_disk: dict[str, dict] = {}


class _FakeStore:
    def __init__(self, hass, version, key):
        self.key = key

    def __class_getitem__(cls, item):
        # 与真实 Store 一致：Store[dict[str, Any]](...) 仍应得到 Store 实例
        return cls

    async def async_load(self):
        return _disk.get(self.key)

    async def async_save(self, data):
        _disk[self.key] = data


_storage_mod = sys.modules["homeassistant.helpers.storage"]
_storage_mod.Store = _FakeStore


# ---------------------------------------------------------------
# 3. 加载 store / const / coordinator
# ---------------------------------------------------------------
def _load(mod_name: str, filename: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(mod_name, PKG_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


_pkg = types.ModuleType(PKG)
_pkg.__path__ = [str(PKG_DIR)]
sys.modules[PKG] = _pkg

const = _load(f"{PKG}.const", "const.py")

_api_stub = types.ModuleType(f"{PKG}.api")
_api_stub.EsLinkApi = type("EsLinkApi", (), {})
_api_stub.EsLinkAuthError = type("EsLinkAuthError", (Exception,), {})
_api_stub.EsLinkError = type("EsLinkError", (Exception,), {})
sys.modules[f"{PKG}.api"] = _api_stub

store = _load(f"{PKG}.store", "store.py")
coordinator = _load(f"{PKG}.coordinator", "coordinator.py")


# ---------------------------------------------------------------
# 4. 测试场景
# ---------------------------------------------------------------
def _build_rows(start: date, end: date, volume_fn) -> list[dict]:
    """生成 [start, end] 闭区间内逐日用量（end 不含当天）。"""
    rows = []
    d = start
    while d < end:
        rows.append({"date": d.isoformat(), "volume": round(volume_fn(d), 4),
                     "amount": 0.0})
        d += timedelta(days=1)
    return rows


class FakeEntry:
    def __init__(self, data):
        self.entry_id = "v143_test"
        self.data = data


def _info_for(meter_reading=207.0, base_date="2026-08-30",
              credit=145.0, cyc_end="2026-12-31", surplus="63|120|99999999"):
    return {
        "user_no": "20061575",
        "meter_no": "104223019630",
        "meter_type": "17",
        "user_address": "示例小区",
        "company_des": "泰能",
        "meter_reading": meter_reading,
        "meter_reading_date": base_date,
        "last_billing_date": base_date,
        "price1": 3.54,
        "cyc_surplus": surplus,
        "cycle_credit_qty": credit,
        "ladder": 1,
        "cyc_end_date": cyc_end,
        "is_default": True,
        "is_active": True,
    }


async def _make_coord(entry: FakeEntry) -> coordinator.TanengGasCoordinator:
    coord = coordinator.TanengGasCoordinator(None, entry)
    coord.user_info = _info_for()
    return coord


def _row_dates(daily_series):
    return [item["date"] for item in daily_series]


def _expect(cond, msg):
    print(("✅ " if cond else "❌ ") + msg)
    if not cond:
        raise AssertionError(msg)


async def main():
    print("=" * 72)
    print("v1.4.3 修复验证：8 月份「每日耗气量」实体在 10 月重启后能否恢复")
    print("=" * 72)

    # ---------- 场景 A：9 月 7 日首次安装，API 一次性回 08-01~09-06 ----------
    entry = FakeEntry({})
    coord = await _make_coord(entry)
    rows_a = _build_rows(date(2026, 8, 1), date(2026, 9, 7),
                          lambda d: 0.4 if d.day == 30 else 0.3)
    rows_a.append({"date": "2026-08-30", "volume": 0.4, "amount": 0.0})
    result_a = coord._compute(coord.user_info, rows_a, months=[],
                              today="2026-09-07")
    daily_a = result_a["daily_series"]
    _expect("2026-08-01" in _row_dates(daily_a),
            f"场景 A: 08-01 在 daily_series（实际 {len(daily_a)} 天）")
    # 模拟 _async_persist_daily_history：把当前 _daily_history 落盘
    await coordinator.TanengGasCoordinator._async_persist_daily_history(coord)
    saved = _disk.get(f"qingdao_taineng_gas.daily_history.{entry.entry_id}")
    _expect(saved and "2026-08-01" in saved["daily"],
            "场景 A: 落盘历史里能找到 08-01（首次落盘成功）")

    # ---------- 场景 B：10 月 12 日重启集成（磁盘历史是 9 月 7 日那份） ----------
    print()
    print("-" * 72)
    print("场景 B: 10-12 重启，ESLink 窗口只回 09-05~10-11，磁盘历史里仍有 8 月")
    print("-" * 72)
    entry_b = FakeEntry({})
    coord_b = await _make_coord(entry_b)
    # 1) 模拟 __init__.py 里在 first_refresh 之前先 load_daily_history
    await coord_b.async_load_daily_history()
    loaded = coord_b.daily_history
    _expect("2026-08-01" in loaded,
            f"场景 B-1: async_load_daily_history 后内存里有 08-01（{len(loaded)} 天）")
    # 2) 10 月的 ESLink 滚动窗口只覆盖到 09-05 之后 37 天
    rows_b = _build_rows(date(2026, 9, 5), date(2026, 10, 12),
                          lambda d: 0.4)
    result_b = coord_b._compute(coord_b.user_info, rows_b, months=[],
                                today="2026-10-12")
    daily_b = result_b["daily_series"]
    _expect("2026-08-01" in _row_dates(daily_b),
            f"场景 B-2: 合并后 08-01 仍在 daily_series（{len(daily_b)} 天）")
    _expect("2026-10-11" in _row_dates(daily_b),
            "场景 B-2: 合并后 10-11 也在 daily_series（新增数据写入）")
    # 8 月份 30 个日期必须全在
    aug_count = sum(1 for d in _row_dates(daily_b) if d.startswith("2026-08"))
    _expect(aug_count == 31,
            f"场景 B-2: 8 月份全部 31 天都恢复（实际 {aug_count}）")
    # 合并后总天数：磁盘历史 37 天 (8-01~9-06) + 本次 API 37 天 (9-05~10-11)
    # 两者在 9-05、9-06 两天重叠（API 优先）→ 实际 37 + 37 - 2 = 72
    _expect(len(daily_b) == 72,
            f"场景 B-2: 合并后总天数 {len(daily_b)}（期望 72，含两天重叠）")

    # ---------- 场景 C：reload 后磁盘历史应包含 10 月新增的日期 ----------
    print()
    print("-" * 72)
    print("场景 C: 落盘后再次重启，依然能拿到 8 月 + 9 月 + 10 月")
    print("-" * 72)
    await coordinator.TanengGasCoordinator._async_persist_daily_history(coord_b)
    entry_c = FakeEntry({})
    coord_c = await _make_coord(entry_c)
    await coord_c.async_load_daily_history()
    _expect("2026-08-01" in coord_c.daily_history,
            "场景 C: 再次 load 后 08-01 仍在")
    _expect("2026-10-11" in coord_c.daily_history,
            "场景 C: 再次 load 后 10-11 也被持久化")
    # 落盘应保留合并后全部 72 天（8 月 31 + 9 月 30 + 10 月 11）
    _expect(len(coord_c.daily_history) == 31 + 30 + 11,
            f"场景 C: 再次 load 后总天数 {len(coord_c.daily_history)}（期望 72）")

    # ---------- 场景 D：边界 —— 磁盘历史为空时，daily_series 仅 API 窗口 ----------
    print()
    print("-" * 72)
    print("场景 D: 全新安装（无磁盘历史），daily_series 等于 API 窗口")
    print("-" * 72)
    # 用全新 entry_id 模拟一个从未被持久化过的全新安装
    entry_d = FakeEntry({})
    entry_d.entry_id = "v143_test_fresh"
    coord_d = await _make_coord(entry_d)
    await coord_d.async_load_daily_history()
    rows_d = _build_rows(date(2026, 9, 5), date(2026, 10, 12),
                          lambda d: 0.3)
    result_d = coord_d._compute(coord_d.user_info, rows_d, months=[],
                                today="2026-10-12")
    daily_d = result_d["daily_series"]
    _expect(daily_d[0]["date"] == "2026-09-05",
            f"场景 D: 全新安装 daily_series 起点为 09-05（实际 {daily_d[0]['date']}）")
    _expect(daily_d[-1]["date"] == "2026-10-11",
            f"场景 D: 全新安装 daily_series 终点为 10-11（实际 {daily_d[-1]['date']}）")

    # ---------- 场景 E：merge_with_history 的优先级（API 优先） ----------
    print()
    print("-" * 72)
    print("场景 E: API 与历史同一天冲突时，API 优先")
    print("-" * 72)
    history = {"2026-08-15": 0.1, "2026-08-20": 0.0}
    api_rows = [{"date": "2026-08-15", "volume": 0.5},
                {"date": "2026-08-25", "volume": 0.2}]
    merged = store.merge_with_history(api_rows, history)
    by_date = {m["date"]: m["volume"] for m in merged}
    _expect(by_date["2026-08-15"] == 0.5,
            f"场景 E: 同日冲突 API 优先（实际 0.5）")
    _expect(by_date["2026-08-20"] == 0.0,
            f"场景 E: 仅历史有的日期保留（实际 0.0）")
    _expect(by_date["2026-08-25"] == 0.2,
            f"场景 E: 仅 API 有的日期新增（实际 0.2）")

    print()
    print("=" * 72)
    print("✅ 全部场景通过：v1.4.3 修复有效")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
