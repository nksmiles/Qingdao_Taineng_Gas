# -*- coding: utf-8 -*-
"""
离线验证「累计表读数」算法（tools 版）
=====================================
在不安装 Home Assistant、也不联网的前提下，用仓库内的 HAR 抓包快照，
验证 coordinator._compute() 的计算是否与验收标准一致。

脚本会自动从 HAR 中定位两个真实接口的响应：
  1. /utility/userBind/getBindUserInfo  —— 官方表读数 / 抄表日期 / 户号 / 表号
  2. /fee/chart/iotBarChart (type=month) —— 日粒度用量

为避免依赖真实运行日期，脚本以「HAR 文件名里的抓包日期」作为参考今天
（本仓库快照抓于 2026-09-06），并校验以下验收值：

    已结算区间     : 2026-08-01 ~ 2026-09-05
    抄表日之后增量 : 0.7+0.6+0.6+0.5+0.4+0.3 = 3.1 m³
    累计表读数     : 210.1 m³   （官方 207.0 @ 2026-08-30 + 3.1）
    最近一日用量   : 0.3 m³（2026-09-05）
    本月累计       : 2.4 m³（9 月已结算日合计，8-31 属 8 月不计入）
    数据滞后       : 1 天

用法（在仓库根目录执行）：
  python3 tools/test_cumulative_reading.py
  python3 tools/test_cumulative_reading.py --include-base-day   # 校准开关=开

可选参数：
  --har <路径>    指定 HAR 文件（默认自动定位仓库根目录下的 channel-*.har.json）
  --today 日期    指定参考“今天”（默认取 HAR 文件名内嵌的抓包日期）

需要 Python 3.9+，仅标准库，无需第三方依赖。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import types
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
PKG = "qingdao_taineng_gas"
PKG_DIR = ROOT / "custom_components" / PKG

# ---------------------------------------------------------------
# 0. 验收标准（来自 CodeBuddy提示词.md 第五节，仅当参考日期为 2026-09-06 时校验）
# ---------------------------------------------------------------
ACCEPT_TODAY = "2026-09-06"
ACCEPT = {
    # include_base_day = False（默认，官方读数不含抄表当天用量）
    False: {
        "cumulative_reading": 210.1,
        "delta": 3.1,
        "daily_usage": 0.3,
        "daily_date": "2026-09-05",
        "month_total": 2.4,
        "data_lag_days": 1,
    },
    # include_base_day = True（官方读数已含抄表当天用量 → 多累加 08-30 的 0.4）
    True: {
        "cumulative_reading": 210.5,
        "delta": 3.5,
    },
}


# ---------------------------------------------------------------
# 1. 注入假的 homeassistant / 包模块，使 coordinator.py 可脱离 HA 导入
#    （仅加载需要测的模块，api.py 用存根代替，因此不需要 aiohttp）
# ---------------------------------------------------------------
def _fake_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


for name in [
    "homeassistant",
    "homeassistant.config_entries",
    "homeassistant.core",
    "homeassistant.exceptions",
    "homeassistant.helpers",
    "homeassistant.helpers.aiohttp_client",
    "homeassistant.helpers.update_coordinator",
]:
    _fake_module(name)

ha = sys.modules["homeassistant"]
ha.config_entries = sys.modules["homeassistant.config_entries"]
ha.core = sys.modules["homeassistant.core"]
ha.exceptions = sys.modules["homeassistant.exceptions"]
ha.helpers = sys.modules["homeassistant.helpers"]
ha.helpers.aiohttp_client = sys.modules["homeassistant.helpers.aiohttp_client"]
ha.helpers.update_coordinator = sys.modules["homeassistant.helpers.update_coordinator"]


class _ConfigEntry:  # 占位
    pass


class _HomeAssistant:  # 占位
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


ha.config_entries.ConfigEntry = _ConfigEntry
ha.core.HomeAssistant = _HomeAssistant
ha.exceptions.ConfigEntryAuthFailed = _ConfigEntryAuthFailed
ha.helpers.aiohttp_client.async_get_clientsession = lambda hass: None
ha.helpers.update_coordinator.DataUpdateCoordinator = _DataUpdateCoordinator
ha.helpers.update_coordinator.UpdateFailed = _UpdateFailed


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
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(PKG_DIR)]
sys.modules[PKG] = pkg

const = _load(f"{PKG}.const", "const.py")

# api 模块用存根代替（离线测试只调 _compute，不会真的发请求）
api_stub = types.ModuleType(f"{PKG}.api")
api_stub.EsLinkApi = type("EsLinkApi", (), {})
api_stub.EsLinkAuthError = type("EsLinkAuthError", (Exception,), {})
api_stub.EsLinkError = type("EsLinkError", (Exception,), {})
sys.modules[f"{PKG}.api"] = api_stub

coordinator_mod = _load(f"{PKG}.coordinator", "coordinator.py")


# ---------------------------------------------------------------
# 2. 从 HAR 提取真实数据
# ---------------------------------------------------------------
def _body_params(entry: dict) -> dict:
    post_data = (entry.get("request") or {}).get("postData") or {}
    text = post_data.get("text") or ""
    return {k: v[0] for k, v in parse_qs(text).items() if v}


def _response_json(entry: dict) -> dict:
    content = (entry.get("response") or {}).get("content") or {}
    text = content.get("text")
    if not text and content.get("encoding") == "base64":
        import base64

        text = base64.b64decode(text or "").decode("utf-8", "replace")
    return json.loads(text or "{}")


BIND_PATH = "/utility/userBind/getBindUserInfo"
CHART_PATH = "/fee/chart/iotBarChart"


def find_har(args: argparse.Namespace) -> Path:
    if args.har:
        return Path(args.har)
    hits = sorted(ROOT.glob("channel-*.har.json"))
    if not hits:
        print(f"❌ 未在 {ROOT} 找到 channel-*.har.json，请用 --har 指定。")
        raise SystemExit(2)
    return hits[0]


def load_snapshot(har_path: Path, today_arg: str | None) -> tuple[dict, list[dict], str]:
    """从 HAR 提取绑定信息 + 日粒度用量，返回 (info, rows, today)。"""
    with open(har_path, encoding="utf-8") as f:
        har = json.load(f)
    entries = har["log"]["entries"]

    # 1) 绑定信息：户号/表号/官方表读数
    bind_entries = [e for e in entries if BIND_PATH in e["request"]["url"]]
    if not bind_entries:
        raise SystemExit("❌ HAR 中找不到 getBindUserInfo 响应。")
    bind_payload = _response_json(bind_entries[0])["result"]
    bind_list = bind_payload.get("userBindList") or []
    if not bind_list:
        raise SystemExit("❌ HAR 中该 SESSION 没有任何绑定表具。")
    item = next((u for u in bind_list if u.get("defaultUser") == 1), bind_list[0])
    meter = (item.get("meterIds") or [{}])[0]

    info = {
        "user_no": str(item.get("userNo") or ""),
        "meter_no": str(item.get("meterNo") or meter.get("meterId") or ""),
        "meter_type": str(item.get("meterType") or meter.get("type") or "17"),
        "meter_reading": float(meter.get("meterReading") or 0),
        # 20260830 → 2026-08-30
        "meter_reading_date": (lambda s: f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s)(
            str(meter.get("meterReadingDate") or "")
        ),
        "is_default": bool(item.get("defaultUser") == 1),
    }

    # 2) 日粒度用量（type=month）
    chart_rows = []
    for e in entries:
        url = e["request"]["url"]
        if CHART_PATH not in url:
            continue
        params = _body_params(e)
        if params.get("type") != "month":
            continue
        detail = _response_json(e).get("result", {}).get("usageDetail") or []
        rows = [
            {"date": str(r["readingTime"]),
             "volume": float(r.get("cycleTotalVolume") or 0),
             "amount": float(r.get("cycleTotalValues") or 0)}
            for r in detail if r.get("readingTime")
        ]
        if rows:
            chart_rows.append(rows)
    if not chart_rows:
        raise SystemExit("❌ HAR 中找不到 type=month 的用量响应。")
    # 同窗口可能有多条请求，取数据最全（范围最大）的一条
    rows = max(chart_rows, key=lambda r: (r[-1]["date"], len(r)))
    rows.sort(key=lambda r: r["date"])

    # 3) 参考“今天”：优先用参数，其次 HAR 文件名内嵌日期，最后用末条日期
    if today_arg:
        today = today_arg
    else:
        m = re.search(r"(\d{4})_(\d{2})_(\d{2})", har_path.name)
        today = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else rows[-1]["date"]

    return info, rows, today


# ---------------------------------------------------------------
# 3. 独立的参考实现（与 coordinator 算法相互对照，防止“两处同错”）
# ---------------------------------------------------------------
def manual_compute(info: dict, rows: list[dict], today: str, include_base_day: bool) -> dict:
    settled = [r for r in rows if r["date"] < today]
    last = settled[-1] if settled else None
    settled_date = last["date"] if last else None

    base = float(info.get("meter_reading") or 0.0)
    base_date = info.get("meter_reading_date")

    delta = 0.0
    if base_date and settled_date:
        for r in settled:
            if include_base_day:
                inside = base_date <= r["date"]
            else:
                inside = base_date < r["date"]
            if inside and r["date"] <= settled_date:
                delta += r["volume"]

    current_month = today[:7]
    month_total = sum(
        r["volume"] for r in settled if r["date"].startswith(current_month)
    )
    return {
        "cumulative_reading": round(base + delta, 4),
        "delta": round(delta, 4),
        "daily_usage": None if last is None else last["volume"],
        "daily_date": settled_date,
        "month_total": round(month_total, 4),
        "data_lag_days": None if settled_date is None
        else (date.fromisoformat(today) - date.fromisoformat(settled_date)).days,
    }


# ---------------------------------------------------------------
# 4. 主流程
# ---------------------------------------------------------------
def _norm(value, digits: int = 4) -> float:
    return round(float(value), digits)


def compare(what: str, actual, expected, errors: list[str]) -> bool:
    same = abs(_norm(actual) - _norm(expected)) < 1e-6
    mark = "✅" if same else "❌"
    print(f"   {mark} {what}: 实际={actual}  期望={expected}")
    if not same:
        errors.append(what)
    return same


def run_case(info, rows, today, include_base_day, label) -> list[str]:
    errors: list[str] = []
    entry = FakeConfigEntry({const.CONF_INCLUDE_BASE_DAY: include_base_day})
    coord = coordinator_mod.TanengGasCoordinator(_HomeAssistant(), entry)
    result = coord._compute(info, rows, today)

    print(f"\n▶ 运行参数：include_base_day = {include_base_day}  参考日期 = {today}")
    print(f"   最后已结算日 = {result['settled_date']}   最近一日用量 = {result['daily_usage']} m³")
    print(f"   本月累计     = {result['month_total']} m³   累计表读数 = {result['cumulative_reading']} m³")
    print(f"   数据滞后     = {result['data_lag_days']} 天   原始条数 = {result['raw_count']}")

    # 与独立参考实现对照
    manual = manual_compute(info, rows, today, include_base_day)
    compare("累计表读数(对照参考实现)", result["cumulative_reading"], manual["cumulative_reading"], errors)
    compare("最近一日用量(对照)", result["daily_usage"], manual["daily_usage"], errors)
    compare("本月累计(对照)", result["month_total"], manual["month_total"], errors)
    if result["settled_date"] != manual["daily_date"]:
        print(f"   ❌ 已结算日期: 实际={result['settled_date']} 期望={manual['daily_date']}")
        errors.append("settled_date")
    compare("数据滞后天数(对照)", result["data_lag_days"], manual["data_lag_days"], errors)

    # 若参考日期匹配验收快照，再对照验收标准
    if today == ACCEPT_TODAY and label == "默认(include_base_day=False)":
        exp = ACCEPT[False]
        compare("累计表读数(验收)", result["cumulative_reading"], exp["cumulative_reading"], errors)
        compare("最近一日用量(验收)", result["daily_usage"], exp["daily_usage"], errors)
        if result["settled_date"] != exp["daily_date"]:
            print(f"   ❌ 最近一日日期(验收): 实际={result['settled_date']} 期望={exp['daily_date']}")
            errors.append("daily_date(验收)")
        compare("本月累计(验收)", result["month_total"], exp["month_total"], errors)
        compare("数据滞后(验收)", result["data_lag_days"], exp["data_lag_days"], errors)

    if today == ACCEPT_TODAY and label == "校准开关=开(include_base_day=True)":
        exp = ACCEPT[True]
        compare("累计表读数(验收)", result["cumulative_reading"], exp["cumulative_reading"], errors)

    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="离线验证累计表读数算法")
    ap.add_argument("--har", default=None, help="HAR 文件路径")
    ap.add_argument("--today", default=None, help="参考今天 YYYY-MM-DD")
    ap.add_argument(
        "--include-base-day",
        action="store_true",
        help="仅运行「校准开关=开」场景（默认两种模式都测）",
    )
    args = ap.parse_args()

    print("=" * 70)
    print("累计表读数算法离线验证（HAR 真实数据）")
    print("=" * 70)

    har_path = find_har(args)
    print(f"HAR: {har_path.name}")
    info, rows, today = load_snapshot(har_path, args.today)
    print(f"官方表读数 : {info['meter_reading']} m³ @ {info['meter_reading_date']}")
    print(f"户号/表号  : {info['user_no']} / {info['meter_no']}")
    print(f"日用量条数 : {len(rows)}（{rows[0]['date']} ~ {rows[-1]['date']}）")
    if today == ACCEPT_TODAY:
        print(f"参考日期   : {today}（= 验收快照日期，将执行验收数值核对）")
    else:
        print(f"参考日期   : {today}（非验收快照日期，仅做算法自洽核对）")

    if args.include_base_day:
        cases = [(True, "校准开关=开(include_base_day=True)")]
    else:
        cases = [(False, "默认(include_base_day=False)"),
                 (True, "校准开关=开(include_base_day=True)")]

    all_errors: list[str] = []
    for include_base_day, label in cases:
        all_errors += run_case(info, rows, today, include_base_day, label)

    print()
    print("-" * 70)
    if all_errors:
        print(f"❌ 存在不一致项：{all_errors}")
        print("   若你替换了 HAR（换了抓包日期），请忽略“验收”项，仅看“对照”项。")
        return 1
    print("✅ 全部校验通过")
    print()
    print("请与微信公众号核对（若参考日期为 2026-09-06）：")
    print("  1. 累计表读数应约为 210.1 m³（官方 207.0 @ 08-30 + 抄表日后 3.1 m³）")
    print("  2. 2026-09-05 当天用量应为 0.3 m³")
    print("  3. 9 月累计（截至 09-05）应为 2.4 m³")
    print("  4. include_base_day=True 时累计表读数应为 210.5 m³（多含 08-30 的 0.4）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
