"""常量定义 —— 青岛泰能燃气（ESLink 易联云）Home Assistant 集成。

所有接口参数均以真实抓包报文为准，详见同目录 README.md。
"""
from __future__ import annotations

import re
from typing import Any

DOMAIN = "qingdao_taineng_gas"
DEFAULT_NAME = "泰能燃气"
MANUFACTURER = "泰能天然气有限公司"

# ---------------------------------------------------------------
# 接口配置（已实测确认：http 与 https 均可，此处统一用 https）
# ---------------------------------------------------------------
BASE_URL = "https://cloudselfhelp-mobile.eslink.cc"

PATH_LOGIN = "/cloudselfhelp/login"                      # token 换 SESSION（本集成不使用）
PATH_BIND_USER = "/utility/userBind/getBindUserInfo"     # 查询户号/表号/当前表读数
PATH_CHART = "/fee/chart/iotBarChart"                    # 用量查询（月/年两种粒度）

# ---------------------------------------------------------------
# 请求约定
# ---------------------------------------------------------------
HEADER_OAUTH_TYPE = "AUTH_MOBILE"   # 固定值，服务端据此识别渠道
CONTENT_TYPE_FORM = "application/x-www-form-urlencoded"
ORIGIN = "http://cloudselfhelp-mobile.eslink.cc"
REFERER = "http://cloudselfhelp-mobile.eslink.cc/?showyqje=0"

RESPONSE_CODE_OK = "100000"         # 外层业务成功码
DEFAULT_METER_TYPE = "17"           # 民用 NB-IoT 物联网表
DEFAULT_TIMEOUT = 20                # 秒

# ---------------------------------------------------------------
# 调度与限制（每日查询计划，v1.2.2 起可在选项里配置）
# ---------------------------------------------------------------
# 燃气数据每日才更新一次，严禁分钟级轮询 —— 会触发风控。
# 用户通过选项设置「每天查询次数」（1~4）与「基础查询时间」，
# 其余查询时间按一天 24 小时均分自动推导（间隔 = 24h / 次数），
# 例如 4 次 + 基础 06:00 → 06:00 / 12:00 / 18:00 / 次日 00:00。
CONF_QUERY_COUNT = "query_count"            # 每天查询次数（1~4）
CONF_BASE_QUERY_TIME = "base_query_time"    # 基础查询时间（HH:MM）
QUERY_COUNT_MIN = 1
QUERY_COUNT_MAX = 4
DEFAULT_QUERY_COUNT = 4
DEFAULT_BASE_QUERY_TIME = "06:00"

# ---------------------------------------------------------------
# 配置键
# ---------------------------------------------------------------
CONF_SESSION = "session"        # SESSION Cookie 值（核心凭据）
CONF_USER_NO = "user_no"        # 户号
CONF_METER_NO = "meter_no"      # 表号
CONF_METER_TYPE = "meter_type"  # 表类型

# 官方抄表读数是否已包含抄表当天的用量。
# 接口返回 meterReading=207.0 / meterReadingDate=2026-08-30，但当天用量为 0.4，
# 无法从接口判断 0.4 是否已计入 207.0。
# False（默认）= 本账期从抄表日的次日起累加；True = 从抄表日当天起累加。
# 该开关同时影响「本账期累计用量」与「预计当前读数」，
# 若与公众号本账期数值相差「抄表当天用量」，在选项里切换此项即可校准。
CONF_INCLUDE_BASE_DAY = "include_base_day"
DEFAULT_INCLUDE_BASE_DAY = False

# ---------------------------------------------------------------
# 传感器属性键
# ---------------------------------------------------------------
ATTR_READING_DATE = "reading_date"                # 数据日期
ATTR_METER_NO = "meter_no"                        # 表号
ATTR_USER_NO = "user_no"                          # 户号（脱敏）
ATTR_METER_READING_DATE = "meter_reading_date"    # 官方抄表日期
ATTR_BASE_READING = "base_reading"                # 官方抄表读数（上个账期结算值）
ATTR_ESTIMATED_READING = "estimated_reading"      # 预计当前读数（官方读数+本账期用量）
ATTR_CYCLE_START = "cycle_start"                  # 本账期起始日（=官方抄表日）
ATTR_CYCLE_END = "cycle_end"                      # 本账期已结算到的日期
ATTR_RECENT_DAYS = "recent_days"                  # 最近 14 天用量明细
ATTR_MONTHLY = "monthly"                          # 当年逐月用量明细（供月度趋势图）
ATTR_SETTLED_DATE = "settled_date"                # 最后已结算日期
ATTR_DATA_LAG = "data_lag_days"                   # 数据滞后天数

# ---- 年度阶梯（燃气费单价 / 当年累计用量用）----
ATTR_ANNUAL_SETTLED = "annual_settled"            # 官方年度已结算累计（cycleCreditQty）
ATTR_ANNUAL_UNBILLED = "annual_unbilled"          # 本年度内抄表后未结算用量
ATTR_ANNUAL_CYCLE_END = "annual_cycle_end"        # 年度计费周期结束日（如 2026-12-31）
ATTR_LADDER = "ladder"                            # 平台当前阶梯（getBindUserInfo.ladder）
ATTR_TIER = "tier"                                # 按当年累计判定的当前适用档 1/2/3
ATTR_TIER_NAME = "tier_name"                      # 档名（第一阶梯…）
ATTR_TIER_THRESHOLDS = "tier_thresholds"          # 各档累计上限
ATTR_TIER_PRICES = "tier_prices"                  # 各档单价（元/m³）
ATTR_TIER_REMAINING = "tier_remaining"            # 接口 cycSurplus（各档剩余量）
ATTR_NEXT_TIER_AT = "next_tier_at"                # 下一档起点累计量
ATTR_NEXT_TIER_REMAINING = "next_tier_remaining"  # 距下一档还差多少 m³

# ---------------------------------------------------------------
# 传感器 key（用于 unique_id）
# ---------------------------------------------------------------
# 上个账期累计表读数（官方抄表读数）
KEY_METER_READING = "meter_reading"
# 最近一日用量
KEY_DAILY_USAGE = "daily_usage"
# 本账期累计用量
KEY_CYCLE_USAGE = "cycle_usage"
# 每日耗气量实体系列前缀，unique_id = {entry_id}_daily_series_YYYY-MM-DD
KEY_DAILY_SERIES = "daily_series_"
# 燃气总用量（累计读数，供能源面板，total_increasing）
KEY_TOTAL_GAS = "total_gas_usage"
# 当年累计用量（户·年累计，1 月 1 日清零，用于核算阶梯计费）
KEY_ANNUAL_USAGE = "annual_usage"
# 燃气费单价（当前边际单价，元/m³）
KEY_GAS_PRICE = "gas_price"

MASK_TAIL_LEN = 4                     # 户号/表号对外展示时保留的末位长度

# ---------------------------------------------------------------
# 泰能燃气阶梯计价（民用气，户·年累计，每年 1 月 1 日清零）
#   * 0 – 228 m³（含）   第一阶梯 3.54 元/m³
#   * 228 – 348 m³（含） 第二阶梯 4.12 元/m³
#   * > 348 m³           第三阶梯 4.99 元/m³
# 跨过 228 / 348 阈值后，后续每立方米自动按更高一档计费。
# 若当地价格政策变化，修改以下三组常量即可。
# ---------------------------------------------------------------
GAS_TIER_THRESHOLDS = (228.0, 348.0)      # 各档累计上限（含）
GAS_TIER_PRICES = (3.54, 4.12, 4.99)      # 元/m³
GAS_TIER_NAMES = ("第一阶梯", "第二阶梯", "第三阶梯")


def tier_for_annual_usage(annual_usage: float | None) -> int | None:
    """按户·年累计用量判定当前适用的阶梯（1/2/3）。"""
    if annual_usage is None:
        return None
    usage = float(annual_usage)
    for index, cap in enumerate(GAS_TIER_THRESHOLDS):
        if usage <= cap:
            return index + 1
    return len(GAS_TIER_PRICES)  # 超过所有档位上限 → 最高档


def gas_price_for_annual_usage(annual_usage: float | None) -> float | None:
    """按户·年累计用量得到当前边际单价（下一 m³ 将适用的阶梯价）。"""
    tier = tier_for_annual_usage(annual_usage)
    if tier is None:
        return None
    return GAS_TIER_PRICES[tier - 1]


# ---------------------------------------------------------------
# 查询计划工具（基础时间 + 每天次数 → 每天的查询时刻）
# ---------------------------------------------------------------
def parse_query_time(value: Any) -> int | None:
    """把基础查询时间解析成当日分钟数（00:00 起）。

    兼容 ``time`` 对象与 ``HH:MM`` / ``H:MM`` / ``HH:MM:SS`` 字符串；
    解析失败返回 None。
    """
    if value is None:
        return None
    hour = getattr(value, "hour", None)
    if hour is not None:
        minute = getattr(value, "minute", 0)
        return hour * 60 + minute
    text = str(value).strip()
    if not text:
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{1,2})?", text)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour * 60 + minute
    return None


def _format_minutes_of_day(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _clamp_query_count(value: Any) -> int:
    """把每天查询次数钳制在合法区间 1~4。"""
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = DEFAULT_QUERY_COUNT
    return max(QUERY_COUNT_MIN, min(QUERY_COUNT_MAX, count))


def query_times_for(
    base_query_time: Any = DEFAULT_BASE_QUERY_TIME,
    query_count: Any = DEFAULT_QUERY_COUNT,
) -> list[str]:
    """推导每天的查询时刻（HH:MM，升序去重）。

    规则：以基础查询时间对齐，把 24 小时按次数均分
    （间隔 = 24h / 次数），其余时刻自动依次后移。
    例如 4 次 + 06:00 → 00:00 / 06:00 / 12:00 / 18:00。
    """
    count = _clamp_query_count(query_count)
    base = parse_query_time(base_query_time)
    if base is None:
        base = parse_query_time(DEFAULT_BASE_QUERY_TIME) or 0
    step = 24 * 60 // count
    minutes = sorted({(base + offset * step) % (24 * 60) for offset in range(count)})
    return [_format_minutes_of_day(m) for m in minutes]
