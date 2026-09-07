"""常量定义 —— 青岛泰能燃气（ESLink 易联云）Home Assistant 集成。

所有接口参数均以真实抓包报文为准，详见同目录 README.md。
"""
from __future__ import annotations

from datetime import timedelta

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
# 调度与限制
# ---------------------------------------------------------------
# 燃气数据每日才更新一次，6 小时足够。
# 严禁分钟级轮询 —— 会触发风控。
UPDATE_INTERVAL = timedelta(hours=6)

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
ATTR_RECENT_DAYS = "recent_days"                  # 最近 7 天用量明细
ATTR_SETTLED_DATE = "settled_date"                # 最后已结算日期
ATTR_DATA_LAG = "data_lag_days"                   # 数据滞后天数

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

MASK_TAIL_LEN = 4                     # 户号/表号对外展示时保留的末位长度
