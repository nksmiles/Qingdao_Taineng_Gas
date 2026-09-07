# -*- coding: utf-8 -*-
"""
离线/在线验证泰能燃气传感器口径（tools 版）
============================================
在不需要 Home Assistant 的情况下，用仓库根目录的 HAR 抓包快照：
  1. /utility/userBind/getBindUserInfo         —— 官方表读数 / 抄表日期 / 户号表号
  2. /fee/chart/iotBarChart (type=month)       —— 日粒度用量（可在线重拉验证）
  3. /fee/chart/iotBarChart (type=year)        —— 月粒度用量（交叉核对自然月求和）
  4. /utility/personalCenter/getBillInfoListPage —— 账单（核对抄表间隔与阶梯用量）

计费口径（已按泰能“非自然月计费”调整）：
  * 官方读数（meterReading）@ 抄表日 = 上个账期结算的累计表读数，仅每月抄表更新
  * 抄表日之后逐日已结算的用量之和 = 「本账期累计用量」（本账期，非自然月）
  * 官方读数 + 本账期累计用量     = 「预计当前读数」（连续估算值，供能源面板等使用）
  * include_base_day=False（默认）本账期从抄表次日(08-31)起算；
    =True 从抄表当天(08-30)起算。两种口径在验收中都会核对。

本仓库当前快照（channel-..._2026_09_07_10_46_57.har.json）预期：
  已结算区间   : 2026-08-01 ~ 2026-09-06（09-07 当天为 0，未结算）
  官方读数     : 207.0 m³ @ 2026-08-30
  本账期累计   : 0.7+0.6+0.6+0.5+0.4+0.3+0.6 = 3.7 m³（默认口径 08-31 起算）
  预计当前读数 : 210.7 m³   （include_base_day=True 时本账期 4.1 / 预计 211.1 m³）
  最近一日用量 : 0.6 m³ @ 2026-09-06
  年度阶梯     : 官方年度已结算累计 cycleCreditQty = 145.0 m³；
                 加本年度未结算增量 3.7 → 「当年累计用量」148.7 m³（默认口径）；
                 未触达 228 阈值 → 边际单价仍为第一阶梯 3.54 元/m³
                 （include_base_day=True 时未结算 4.1 → 当年累计 149.1）
  账期核对     : 07-30 抄表 199.0 → 08-30 抄表 207.0，间隔 31 天，
                 账单 8.0 m³ 全部为第一阶梯 3.54 元/m³ → 应缴 28.32 元，
                 与日数据窗口内 08-01~08-30 合计 7.4 m³ + 窗口外 07-31（0.6）吻合。

用法（在仓库根目录执行）：
  py tools/test_cumulative_reading.py                  # 离线验证全部 channel-*.har.json
  py tools/test_cumulative_reading.py --include-base-day   # 只跑校准开关=开
  py tools/test_cumulative_reading.py --online         # 额外用 HAR 中的 SESSION 在线重拉验证
  py tools/test_cumulative_reading.py --har <路径>     # 指定 HAR
  py tools/test_cumulative_reading.py --today 2026-09-07

需要 Python 3.9+，仅标准库（--online 使用 urllib）。
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import re
import sys
import types
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

# Windows 控制台默认编码（GBK）无法输出 m³ 等字符，脚本自行切到 UTF-8
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # noqa: PERF203
        pass

ROOT = Path(__file__).resolve().parent.parent
PKG = "qingdao_taineng_gas"
PKG_DIR = ROOT / "custom_components" / PKG

# ---------------------------------------------------------------
# 0. 已知快照的静态验收值（以 HAR 文件名内嵌的抓包日期为参考“今天”）
# ---------------------------------------------------------------
SNAPSHOTS = {
    # 旧快照：抓于 2026-09-06（已结算到 09-05）
    "2026-09-06": {
        False: {"cycle_total": 3.1, "estimated_reading": 210.1,
                "daily_usage": 0.3, "settled_date": "2026-09-05", "data_lag_days": 1},
        True: {"cycle_total": 3.5, "estimated_reading": 210.5},
    },
    # 新快照：抓于 2026-09-07（已结算到 09-06；年度字段按 cycleCreditQty=145.0）
    "2026-09-07": {
        False: {"cycle_total": 3.7, "estimated_reading": 210.7,
                "daily_usage": 0.6, "settled_date": "2026-09-06", "data_lag_days": 1,
                "annual_settled": 145.0, "annual_unbilled": 3.7, "annual_usage": 148.7},
        True: {"cycle_total": 4.1, "estimated_reading": 211.1,
               "annual_unbilled": 4.1, "annual_usage": 149.1},
    },
}


# ---------------------------------------------------------------
# 1. 注入假的 homeassistant / 包模块，使 coordinator.py 可脱离 HA 导入
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
    "homeassistant.helpers.update_coordinator",
]:
    _fake_module(_name)

_ha = sys.modules["homeassistant"]
_ha.config_entries = sys.modules["homeassistant.config_entries"]
_ha.core = sys.modules["homeassistant.core"]
_ha.exceptions = sys.modules["homeassistant.exceptions"]
_ha.helpers = sys.modules["homeassistant.helpers"]
_ha.helpers.aiohttp_client = sys.modules["homeassistant.helpers.aiohttp_client"]
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
    """极简替身：只保留被测代码需要的接口。"""

    def __class_getitem__(cls, item):  # 支持 DataUpdateCoordinator[dict[str, Any]]
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


class FakeConfigEntry:
    """给协调器用的假配置条目。"""

    def __init__(self, data: dict):
        self.entry_id = "offline_test"
        self.data = data


def _load(mod_name: str, filename: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(mod_name, PKG_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# 建立包结构，使 coordinator 内的相对导入可用
_pkg = types.ModuleType(PKG)
_pkg.__path__ = [str(PKG_DIR)]
sys.modules[PKG] = _pkg

const = _load(f"{PKG}.const", "const.py")

# api 模块用存根代替（离线只调 _compute，不会真的发请求）
_api_stub = types.ModuleType(f"{PKG}.api")
_api_stub.EsLinkApi = type("EsLinkApi", (), {})
_api_stub.EsLinkAuthError = type("EsLinkAuthError", (Exception,), {})
_api_stub.EsLinkError = type("EsLinkError", (Exception,), {})
sys.modules[f"{PKG}.api"] = _api_stub

coordinator_mod = _load(f"{PKG}.coordinator", "coordinator.py")


# ---------------------------------------------------------------
# 2. 从 HAR 提取真实数据
# ---------------------------------------------------------------
def _body_params(entry: dict) -> dict:
    text = ((entry.get("request") or {}).get("postData") or {}).get("text") or ""
    return {k: v[0] for k, v in parse_qs(text).items() if v}


def _response_text(entry: dict) -> str:
    content = (entry.get("response") or {}).get("content") or {}
    text = content.get("text") or ""
    if not text and content.get("encoding") == "base64":
        text = base64.b64decode(text or "").decode("utf-8", "replace")
    return text


def _response_json(entry: dict) -> dict:
    return json.loads(_response_text(entry) or "{}")


def _norm_reading_date(value) -> str:
    """20260830 → 2026-08-30。"""
    s = str(value or "").strip()
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s


def _har_files(args: argparse.Namespace) -> list[Path]:
    if args.har:
        return [Path(args.har)]
    hits = sorted(ROOT.glob("channel-*.har.json"))
    if not hits:
        print(f"❌ 未在 {ROOT} 找到 channel-*.har.json，请用 --har 指定。")
        raise SystemExit(2)
    return hits


def load_snapshot(har_path: Path, today_arg: str | None) -> dict:
    """提取绑定信息 / 日用量 / 月用量 / 账单 / SESSION。"""
    with open(har_path, encoding="utf-8") as f:
        har = json.load(f)
    entries = har["log"]["entries"]

    session = ""
    for e in entries:
        for h in e["request"].get("headers") or []:
            if h.get("name", "").lower() == "cookie":
                for part in (h.get("value", "") or "").split(";"):
                    if part.strip().upper().startswith("SESSION="):
                        session = part.strip().split("=", 1)[1]
                        break

    # 1) 绑定信息
    bind_entries = [e for e in entries if "/utility/userBind/getBindUserInfo" in e["request"]["url"]]
    if not bind_entries:
        raise SystemExit(f"❌ {har_path.name} 中找不到 getBindUserInfo 响应。")
    bind_list = _response_json(bind_entries[0]).get("result", {}).get("userBindList") or []
    if not bind_list:
        raise SystemExit(f"❌ {har_path.name} 中该 SESSION 没有任何绑定表具。")
    item = next((u for u in bind_list if u.get("defaultUser") == 1), bind_list[0])
    meter = (item.get("meterIds") or [{}])[0]
    info = {
        "user_no": str(item.get("userNo") or ""),
        "meter_no": str(item.get("meterNo") or meter.get("meterId") or ""),
        "meter_type": str(item.get("meterType") or meter.get("type") or "17"),
        "meter_reading": float(meter.get("meterReading") or 0),
        "meter_reading_date": _norm_reading_date(meter.get("meterReadingDate")),
        "is_default": bool(item.get("defaultUser") == 1),
        # 年度阶梯相关字段（cycleCreditQty=官方年度已结算累计，用于阶梯核对）
        "cycle_credit_qty": (
            float(meter.get("cycleCreditQty")) if meter.get("cycleCreditQty") not in (None, "") else None
        ),
        "ladder": (
            int(meter.get("ladder")) if meter.get("ladder") not in (None, "") else None
        ),
        "cyc_end_date": _norm_reading_date(meter.get("cycEndDate")),
        "cyc_surplus": meter.get("cycSurplus") or "",
        "price1": float(meter.get("price1") or 0),
    }

    # 2) 日粒度 / 月粒度用量
    day_candidates, month_rows = [], []
    for e in entries:
        if "/fee/chart/iotBarChart" not in e["request"]["url"]:
            continue
        params = _body_params(e)
        payload = _response_json(e).get("result", {}).get("usageDetail") or []
        if not payload:
            continue
        if params.get("type") == "month":
            rows = [
                {"date": str(r.get("readingTime")),
                 "volume": float(r.get("cycleTotalVolume") or 0),
                 "amount": float(r.get("cycleTotalValues") or 0)}
                for r in payload if r.get("readingTime")
            ]
            if rows:
                day_candidates.append(rows)
        elif params.get("type") == "year":
            month_rows = [
                {"date": str(r.get("readingTime")), "volume": float(r.get("cycleTotalVolume") or 0)}
                for r in payload if r.get("readingTime")
            ]
    if not day_candidates:
        raise SystemExit(f"❌ {har_path.name} 中找不到 type=month 的日用量响应。")
    rows = max(day_candidates, key=lambda r: (r[-1]["date"], len(r)))
    rows.sort(key=lambda r: r["date"])
    month_rows.sort(key=lambda r: r["date"])

    # 3) 账单列表
    bills: list[dict] = []
    for e in entries:
        if "/utility/personalCenter/getBillInfoListPage" not in e["request"]["url"]:
            continue
        bills = _response_json(e).get("result", {}).get("billDetailList") or []
        break

    # 4) 参考“今天”：优先参数 → HAR 文件名内嵌日期 → 日数据末条日期
    if today_arg:
        today = today_arg
    else:
        m = re.search(r"(\d{4})_(\d{2})_(\d{2})", har_path.name)
        today = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else rows[-1]["date"]

    return {
        "info": info,
        "rows": rows,
        "month_rows": month_rows,
        "bills": bills,
        "session": session,
        "today": today,
        "har": har_path,
    }


# ---------------------------------------------------------------
# 3. 独立的参考实现（与 coordinator 算法相互对照）
# ---------------------------------------------------------------
def manual_compute(info: dict, rows: list[dict], today: str, include_base_day: bool) -> dict:
    settled = [r for r in rows if r["date"] < today]
    last = settled[-1] if settled else None
    settled_date = last["date"] if last else None

    base = float(info.get("meter_reading") or 0.0)
    base_date = info.get("meter_reading_date")

    cycle_total = None
    if base_date and settled_date and settled_date >= base_date:
        cycle_total = 0.0
        for r in settled:
            inside = (base_date <= r["date"]) if include_base_day else (base_date < r["date"])
            if inside and r["date"] <= settled_date:
                cycle_total += r["volume"]
        cycle_total = round(cycle_total, 4)
    estimated = round(base + (cycle_total or 0.0), 4)

    # 年度阶梯（与 coordinator._compute 的「官方已结算 + 本年度未结算」口径一致）
    year = date.fromisoformat(today).year
    cyc_end = info.get("cyc_end_date") or ""
    credit = info.get("cycle_credit_qty")
    annual_settled = None
    if credit is not None:
        cyc_year = year
        if len(cyc_end) >= 4 and cyc_end[:4].isdigit():
            cyc_year = int(cyc_end[:4])
        annual_settled = round(float(credit), 4) if cyc_year == year else 0.0
    annual_unbilled = 0.0
    if base_date and settled_date and settled_date >= base_date:
        annual_unbilled = round(sum(
            r["volume"] for r in settled
            if r["date"].startswith(str(year))
            and (base_date <= r["date"] if include_base_day else base_date < r["date"])
            and r["date"] <= settled_date
        ), 4)
    annual_usage = round(annual_settled + annual_unbilled, 4) if annual_settled is not None else None

    recent = [
        {"date": r["date"], "volume": round(r["volume"], 4)}
        for r in settled[-7:]
    ]
    daily_series = [
        {"date": r["date"], "volume": round(r["volume"], 4)} for r in settled
    ]
    return {
        "official_reading": base,
        "cycle_total": cycle_total,
        "estimated_reading": estimated,
        "daily_usage": None if last is None else last["volume"],
        "settled_date": settled_date,
        "data_lag_days": None if settled_date is None
        else (date.fromisoformat(today) - date.fromisoformat(settled_date)).days,
        "recent": recent,
        "daily_series": daily_series,
        "annual_settled": annual_settled,
        "annual_unbilled": annual_unbilled,
        "annual_usage": annual_usage,
    }


# ---------------------------------------------------------------
# 4. 校验工具
# ---------------------------------------------------------------
def _norm(value, digits: int = 4) -> float:
    return round(float(value), digits)


def compare(what: str, actual, expected, errors: list[str]) -> bool:
    same = abs(_norm(actual) - _norm(expected)) < 1e-6
    print(f"   {'✅' if same else '❌'} {what}: 实际={actual}  期望={expected}")
    if not same:
        errors.append(what)
    return same


def expect_equal(what: str, actual, expected, errors: list[str]) -> bool:
    same = actual == expected
    print(f"   {'✅' if same else '❌'} {what}: 实际={actual}  期望={expected}")
    if not same:
        errors.append(what)
    return same


def run_sensor_cases(snap: dict, include_flag: bool | None) -> list[str]:
    """运行 coordinator._compute，并与参考实现 / 静态验收值对照。"""
    errors: list[str] = []
    info, rows, today = snap["info"], snap["rows"], snap["today"]
    flags = [(False, "默认(include_base_day=False)"), (True, "校准开关=开(True)")]
    if include_flag is not None:
        flags = [f for f in flags if f[0] == include_flag]

    for include_base_day, label in flags:
        entry = FakeConfigEntry({const.CONF_INCLUDE_BASE_DAY: include_base_day})
        coord = coordinator_mod.TanengGasCoordinator(_HomeAssistant(), entry)
        result = coord._compute(info, rows, today)

        print(f"\n▶ {label}  参考日期 = {today}")
        print(f"   官方读数(上个账期) = {result['official_reading']} m³ @ {result['base_date']}")
        print(f"   本账期累计 = {result['cycle_total']} m³   预计当前读数 = {result['estimated_reading']} m³")
        print(f"   最近一日用量 = {result['daily_usage']} m³ @ {result['settled_date']}   数据滞后 = {result['data_lag_days']} 天")
        print(f"   日数据原始条数 = {result['raw_count']}   每日耗气量实体数 = {len(result['daily_series'])}")
        print(f"   官方年度已结算 = {result.get('annual_settled')} m³   "
              f"本年度未结算增量 = {result.get('annual_unbilled')} m³")
        print(f"   当年累计用量 = {result.get('annual_usage')} m³   "
              f"燃气费单价(边际) = {const.gas_price_for_annual_usage(result.get('annual_usage'))} 元/m³   "
              f"档位 = {const.tier_for_annual_usage(result.get('annual_usage'))}")

        # 与参考实现对照（防“两处同错”）
        manual = manual_compute(info, rows, today, include_base_day)
        compare("官方读数(对照)", result["official_reading"], manual["official_reading"], errors)
        compare("本账期累计(对照)", result["cycle_total"], manual["cycle_total"], errors)
        compare("预计当前读数(对照)", result["estimated_reading"], manual["estimated_reading"], errors)
        compare("最近一日用量(对照)", result["daily_usage"], manual["daily_usage"], errors)
        expect_equal("已结算日期(对照)", result["settled_date"], manual["settled_date"], errors)
        compare("数据滞后(对照)", result["data_lag_days"], manual["data_lag_days"], errors)
        expect_equal("每日耗气量明细(对照)", result["daily_series"], manual["daily_series"], errors)
        expect_equal("最近7天(对照)", result["recent_days"], manual["recent"], errors)
        compare("当年累计用量(对照)", result.get("annual_usage"), manual.get("annual_usage"), errors)
        compare("官方年度已结算(对照)", result.get("annual_settled"), manual.get("annual_settled"), errors)
        compare("年度未结算增量(对照)", result.get("annual_unbilled"), manual.get("annual_unbilled"), errors)

        # 若为已知快照，再与静态验收值核对
        exp = SNAPSHOTS.get(today, {}).get(include_base_day)
        if exp is not None:
            compare("本账期累计(验收)", result["cycle_total"], exp["cycle_total"], errors)
            compare("预计当前读数(验收)", result["estimated_reading"], exp["estimated_reading"], errors)
            if include_base_day is False:
                compare("最近一日用量(验收)", result["daily_usage"], exp["daily_usage"], errors)
                expect_equal("已结算日期(验收)", result["settled_date"], exp["settled_date"], errors)
                compare("数据滞后(验收)", result["data_lag_days"], exp["data_lag_days"], errors)
                expect_equal("官方读数(验收)", result["official_reading"], info["meter_reading"], errors)
            # 年度阶梯静态验收（仅当快照提供了对应期望值）
            if exp.get("annual_settled") is not None:
                compare("官方年度已结算(验收)", result.get("annual_settled"), exp["annual_settled"], errors)
            if exp.get("annual_unbilled") is not None:
                compare("年度未结算增量(验收)", result.get("annual_unbilled"), exp["annual_unbilled"], errors)
            if exp.get("annual_usage") is not None:
                compare("当年累计用量(验收)", result.get("annual_usage"), exp["annual_usage"], errors)
                compare(
                    "燃气费单价(验收)",
                    const.gas_price_for_annual_usage(result.get("annual_usage")),
                    const.gas_price_for_annual_usage(exp["annual_usage"]),
                    errors,
                )
    return errors


# ---------------------------------------------------------------
# 5. 抄表间隔 / 阶梯用量核对
# ---------------------------------------------------------------
def verify_billing(snap: dict) -> list[str]:
    """依据「两个抄表日期的间隔」与账单阶梯，核对用量是否符合预期。"""
    errors: list[str] = []
    info, rows, today = snap["info"], snap["rows"], snap["today"]
    bills = snap["bills"]

    print("\n▶ 抄表间隔 / 阶梯用量核对（账单历史）")
    if not bills:
        print("   ⚠️ HAR 中没有 getBillInfoListPage 账单，跳过阶梯核对。")
        return errors

    print(f"   账单数 = {len(bills)}，账单顺序（新→旧）：")
    for b in bills:
        tiers = b.get("cumulativeUsages") or b.get("useDetailList") or []
        des = "/".join(f"{t.get('feeTypeDes')}{t.get('feeQuantity')}@{t.get('unitPrice')}" for t in tiers)
        print(f"     {b.get('billDate')}  读表 {_norm_reading_date(b.get('meterReadTime'))}  "
              f"{b.get('lastNum')} → {b.get('thisNum')}  useNum={b.get('useNum')}  "
              f"shouldFee={b.get('shouldFee')}  [{des}]")

    # 5.1 每张账单：读数差 == 用量 == 各阶梯用量之和，金额 == Σ各档金额
    for b in bills:
        try:
            diff = round(float(b.get("thisNum") or 0) - float(b.get("lastNum") or 0), 4)
            use = float(b.get("useNum") or 0)
            tiers = b.get("cumulativeUsages") or b.get("useDetailList") or []
            tier_qty = round(sum(float(t.get("feeQuantity") or 0) for t in tiers), 4)
            # 各档金额以账单自身金额为准；缺失时用量×单价并按 2 位舍入
            tier_fee = 0.0
            for t in tiers:
                tf = t.get("shouldFee")
                if tf in (None, ""):
                    tf = float(t.get("feeQuantity") or 0) * float(t.get("unitPrice") or 0)
                tier_fee += float(tf or 0)
            tier_fee = round(tier_fee, 2)
        except (TypeError, ValueError):
            continue
        tag = b.get("billDate")
        compare(f"账单{tag} 读数差==useNum", diff, use, errors)
        compare(f"账单{tag} useNum==阶梯量合计", use, tier_qty, errors)
        compare(f"账单{tag} 应缴==各档金额合计",
                round(float(b.get("shouldFee") or 0), 2), tier_fee, errors)

    # 5.2 最近两期抄表间隔（当前账期由 getBindUserInfo 提供）
    last_bill = bills[0]  # 新→旧
    base_date = info.get("meter_reading_date")
    if last_bill.get("billDate"):
        bd = _norm_reading_date(last_bill["billDate"])
        if bd == base_date:
            prev_date = _norm_reading_date(bills[1]["billDate"]) if len(bills) > 1 else None
            prev_num = float(bills[1].get("thisNum") or 0) if len(bills) > 1 else None
            if prev_date:
                days = (date.fromisoformat(base_date) - date.fromisoformat(prev_date)).days
                print(f"   抄表间隔 = {days} 天")
            bill_qty = float(last_bill.get("useNum") or 0)
            compare("账单用量(上期)", bill_qty, round(float(info["meter_reading"]) - prev_num, 4), errors)

            # 用日数据核对上期账单：日窗口内 (上一抄表日, 本抄表日] 的可见部分
            window_in = 0.0
            window_days = 0
            for r in rows:
                if prev_date and prev_date < r["date"] <= base_date:
                    window_in += r["volume"]
                    window_days += 1
            print(f"   日数据窗口内(08-01~{base_date})可核对部分 = {round(window_in, 4)} m³（{window_days} 天）")
            print(f"   差额 {round(bill_qty - window_in, 4)} m³ 在窗口外（07-31 等），账期共 "
                  f"{(date.fromisoformat(base_date) - date.fromisoformat(prev_date)).days} 天 > 窗口 {window_days} 天")
            if window_in > bill_qty + 1e-6:
                errors.append("上期账单日数据可见部分超过账单量")
                print("   ❌ 日数据可见部分已大于账单量，存在矛盾！")
            else:
                print("   ✅ 窗口内日用量未超出账单量，缺口可由窗口外日期解释，账期核对通过。")

    # 5.3 年度阶梯累计：2026 年账单合计 == cycleCreditQty（年度累计第一档）
    year_total = round(sum(float(b.get("useNum") or 0)
                           for b in bills if str(b.get("billDate") or "").startswith("2026")), 4)
    credit = info.get("cycle_credit_qty")
    print(f"\n   2026 年度账单累计用量 = {year_total} m³   接口 cycleCreditQty = {credit} m³")
    if credit is not None:
        compare("年度账单合计==cycleCreditQty", year_total, credit, errors)

    # 5.4 全部账单均第一阶梯（用量未触达第二档），与 isMultipleLadders/价格一致
    tiers_seen = {t.get("feeTypeDes") for b in bills
                  for t in (b.get("cumulativeUsages") or [])}
    print(f"   账单中出现过的阶梯档位 = {sorted(tiers_seen)}")
    if tiers_seen and tiers_seen != {"第一阶梯"}:
        errors.append("账单阶梯档位超出第一档")
    return errors


# ---------------------------------------------------------------
# 6. 月粒度 / 日粒度交叉核对（证明逐日数据与月视图一致、可直接获取）
# ---------------------------------------------------------------
def verify_month_rows(snap: dict) -> list[str]:
    errors: list[str] = []
    rows, month_rows, today = snap["rows"], snap["month_rows"], snap["today"]
    if not month_rows:
        return errors
    print("\n▶ 月视图与逐日数据交叉核对（自然月求和，仅对比日窗口能覆盖的月份）")
    # 日窗口从 rows[0] 开始，仅当该月 1 号起在窗口内时，日求和才是完整的
    first_covered_month = rows[0]["date"][:7]
    by_month: dict[str, float] = {}
    for r in rows:
        if r["date"] < today:
            by_month[r["date"][:7]] = by_month.get(r["date"][:7], 0.0) + r["volume"]
    for m in month_rows:
        key = m["date"]
        if key < first_covered_month:
            print(f"   ⏭️ 月{key}：早于日窗口起点，跳过")
            continue
        daily_sum = round(by_month.get(key, 0.0), 4)
        chart = round(float(m["volume"]), 4)
        expect_equal(f"月{key} 日求和==月视图({chart})", daily_sum, chart, errors)
    return errors


# ---------------------------------------------------------------
# 7. 在线重拉验证（可选）：用 HAR 中的 SESSION 直接请求接口
# ---------------------------------------------------------------
def verify_online(snap: dict) -> list[str]:
    """用 HAR 内的 SESSION 在线请求，核对是否与快照一致（可直接获取）。"""
    import ssl
    import urllib.error
    import urllib.request

    errors: list[str] = []
    session, info, rows, today = snap["session"], snap["info"], snap["rows"], snap["today"]
    host = "cloudselfhelp-mobile.eslink.cc"
    headers = {
        "oAuthType": "AUTH_MOBILE",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 "
                       "MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat"),
        "Origin": f"http://{host}",
        "Referer": f"http://{host}/",
        "Cookie": f"SESSION={session}",
    }

    def post(path: str, data: dict):
        body = "&".join(f"{k}={v}" for k, v in data.items()).encode()
        req = urllib.request.Request(f"https://{host}{path}", data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=25, context=ssl.create_default_context()) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"   在线验证失败（网络被网关拦截？）：{type(e).__name__}: {e}")

    print(f"\n▶ 在线重拉验证（SESSION={session[:8]}…{session[-4:]}，非本机家庭宽带会被网关拦截）")
    code, text = post("/utility/userBind/getBindUserInfo", {"includeStoppedMeter": "true", "token": ""})
    if code != 200:
        raise SystemExit(f"   getBindUserInfo HTTP {code}，无法在线验证。")
    j = json.loads(text)
    if j.get("responseCode") != "100000":
        raise SystemExit(f"   getBindUserInfo 业务失败：{j.get('message')}")
    bind_list = j.get("result", {}).get("userBindList") or []
    item = next((u for u in bind_list if u.get("defaultUser") == 1), bind_list[0])
    meter = (item.get("meterIds") or [{}])[0]
    compare("在线 官方读数==快照", float(meter.get("meterReading") or 0), info["meter_reading"], errors)
    expect_equal("在线 抄表日期==快照",
                 _norm_reading_date(meter.get("meterReadingDate")), info["meter_reading_date"], errors)

    code, text = post("/fee/chart/iotBarChart", {
        "userNo": info["user_no"], "meterNo": info["meter_no"], "type": "month", "meterType": info["meter_type"],
        "startTime": "", "endTime": "", "time": "2026-09",
    })
    if code != 200:
        raise SystemExit(f"   iotBarChart HTTP {code}，无法在线验证。")
    j = json.loads(text)
    detail = j.get("result", {}).get("usageDetail") or []
    live_rows = sorted(
        ({"date": str(r.get("readingTime")), "volume": float(r.get("cycleTotalVolume") or 0),
          "amount": float(r.get("cycleTotalValues") or 0)}
         for r in detail if r.get("readingTime")),
        key=lambda r: r["date"],
    )
    print(f"   在线日数据 {live_rows[0]['date']} ~ {live_rows[-1]['date']} 共 {len(live_rows)} 条")
    expect_equal("在线 逐日数据==快照", live_rows, rows, errors)
    return errors


# ---------------------------------------------------------------
# 8. 主流程
# ---------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="离线/在线验证泰能燃气传感器口径与阶梯用量")
    ap.add_argument("--har", default=None, help="HAR 文件路径（默认遍历仓库根目录 channel-*.har.json）")
    ap.add_argument("--today", default=None, help="参考今天 YYYY-MM-DD")
    ap.add_argument("--include-base-day", action="store_true",
                    help="仅运行「校准开关=开」场景（默认两种都测）")
    ap.add_argument("--online", action="store_true", help="额外用 HAR 中的 SESSION 在线重拉验证")
    args = ap.parse_args()

    print("=" * 72)
    print("泰能燃气传感器口径验证（官方读数 / 本账期累计 / 阶梯用量）")
    print("=" * 72)

    all_errors: list[str] = []
    for har_path in _har_files(args):
        print(f"\n{'─' * 72}\nHAR: {har_path.name}")
        snap = load_snapshot(har_path, args.today)
        info, rows, today = snap["info"], snap["rows"], snap["today"]
        print(f"官方读数 : {info['meter_reading']} m³ @ {info['meter_reading_date']}   "
              f"户号/表号 : {info['user_no']} / {info['meter_no']}   "
              f"SESSION: {snap['session'][:8]}…{snap['session'][-4:]}")
        print(f"日数据条数 : {len(rows)}（{rows[0]['date']} ~ {rows[-1]['date']}）   参考日期 : {today}")

        flag = True if args.include_base_day else None
        all_errors += run_sensor_cases(snap, flag)
        all_errors += verify_billing(snap)
        all_errors += verify_month_rows(snap)
        if args.online:
            all_errors += verify_online(snap)

    print()
    print("-" * 72)
    if all_errors:
        print(f"❌ 存在不一致项：{sorted(set(all_errors))}")
        return 1
    print("✅ 全部校验通过")
    print()
    print("请与微信公众号核对（参考日期 2026-09-07）：")
    print("  1. 上个账期累计表读数（官方读数）应为 207.0 m³，抄表日 2026-08-30")
    print("  2. 本账期累计用量（默认口径）应为 3.7 m³，预计当前读数 210.7 m³")
    print("  3. 最近一日用量应为 0.6 m³（2026-09-06）")
    print("  4. include_base_day=True 时本账期累计用量应为 4.1 m³（多含 08-30 的 0.4）")
    print("  5. 上期账单 8.0 m³ 全为第一阶梯 3.54 元/m³ → 应缴 28.32 元，与抄表间隔/日数据吻合")
    print("  6. 当年累计用量应为 148.7 m³（官方已结算 145.0 + 本年度未结算 3.7），")
    print("     燃气费单价仍为第一阶梯 3.54 元/m³（当年累计未跨 228 m³ 阈值）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
