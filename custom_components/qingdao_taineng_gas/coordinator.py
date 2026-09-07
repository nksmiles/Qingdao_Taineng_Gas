"""数据协调器 —— 负责拉取数据并计算各传感器需要的数值。"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EsLinkApi, EsLinkAuthError, EsLinkError
from .const import (
    CONF_INCLUDE_BASE_DAY,
    CONF_METER_NO,
    CONF_METER_TYPE,
    CONF_SESSION,
    CONF_USER_NO,
    DEFAULT_INCLUDE_BASE_DAY,
    DEFAULT_METER_TYPE,
    DOMAIN,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

RECENT_DAYS = 7


def _mask(value: str, tail: int = 4) -> str:
    """对户号/表号做脱敏，仅保留末几位用于识别。"""
    if not value:
        return ""
    if len(value) <= tail:
        return value
    return f"*{value[-tail:]}"


def _parse_tier_remaining(raw: Any) -> list[float]:
    """把接口 cycSurplus（如 "83.00|120|99999999"）解析成各档剩余量列表。"""
    values: list[float] = []
    if not raw:
        return values
    for chunk in str(raw).split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            values.append(float(chunk))
        except ValueError:
            _LOGGER.debug("无法解析 cycSurplus 分量：%s", chunk)
    return values


class TanengGasCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """泰能燃气数据协调器。"""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.user_info: dict[str, Any] = {}
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )

    @property
    def _api(self) -> EsLinkApi:
        return EsLinkApi(
            async_get_clientsession(self.hass),
            self.entry.data[CONF_SESSION],
        )

    # ------------------------------------------------------------------
    async def _async_update_data(self) -> dict[str, Any]:
        """拉取并计算全部数据。"""
        api = self._api
        try:
            # 1) 户号 / 表号 / 官方表读数（每次都拉，便于发现绑定变更与读数刷新）
            users = await api.get_bind_user_info()
        except EsLinkAuthError as err:
            raise ConfigEntryAuthFailed(f"登录凭据已失效，请更新 SESSION：{err}") from err
        except EsLinkError as err:
            raise UpdateFailed(f"获取户号信息失败：{err}") from err

        user_no = self.entry.data.get(CONF_USER_NO) or ""
        meter_no = self.entry.data.get(CONF_METER_NO) or ""

        info = None
        if user_no or meter_no:
            for item in users:
                if user_no and item["user_no"] != user_no:
                    continue
                if meter_no and item["meter_no"] != meter_no:
                    continue
                info = item
                break
        if info is None:
            # 未指定或匹配不到时，优先取默认用户，否则取第一个
            info = next((u for u in users if u["is_default"]), users[0])

        self.user_info = info

        meter_type = self.entry.data.get(CONF_METER_TYPE) or info.get("meter_type") or DEFAULT_METER_TYPE

        try:
            rows = await api.get_daily_usage(info["user_no"], info["meter_no"], meter_type)
        except EsLinkAuthError as err:
            raise ConfigEntryAuthFailed(f"登录凭据已失效，请更新 SESSION：{err}") from err
        except EsLinkError as err:
            raise UpdateFailed(f"获取用量数据失败：{err}") from err

        return self._compute(info, rows)

    # ------------------------------------------------------------------
    def _compute(
        self,
        info: dict[str, Any],
        rows: list[dict[str, Any]],
        today: str | None = None,
    ) -> dict[str, Any]:
        """根据官方表读数与日用量，计算各传感器数值。

        :param today: 参考日期（ISO 格式）。默认取当天；
            测试时可显式传入，使离线验证不依赖运行时的真实日期。

        计费口径说明（已结合泰能燃气“非自然月”计费特性）：
            泰能每月在月末抄表结算一次，抄表日即上个账期的结算截止日。
            官方读数（meterReading）@ 抄表日 = 上个账期结算的累计表读数；
            抄表日之后逐日结算的用量，累加形成「本账期累计用量」。
            抄表日当天用量是否已计入官方读数无法从接口判断，
            因此「本账期」起算点由 include_base_day 开关控制（见 const.py 注释）。

        各传感器数值：
            official_reading  : 官方读数（上个账期累计表读数，抄表日更新）
            cycle_total       : 本账期累计用量（抄表日之后已结算用量之和）
            estimated_reading : 预计当前读数 = 官方读数 + 本账期累计用量
                                （保留连续估算值，即「燃气总用量」实体值，供能源面板使用）
            annual_settled    : 官方年度已结算累计（getBindUserInfo.cycleCreditQty）
            annual_unbilled   : 本年度内抄表后仍未结算的用量增量
            annual_usage      : 当年累计用量 = annual_settled + annual_unbilled
                                （供「当年累计用量」与「燃气费单价」传感器使用）
        """
        if not today:
            today = date.today().isoformat()

        # 数据滞后一天：当天那条通常为 0（未结算），故排除当天
        settled = [r for r in rows if r["date"] < today]
        last_settled = settled[-1] if settled else None
        settled_date = last_settled["date"] if last_settled else None

        # 1) 最近一日用量（取已结算的最后一天）
        daily_volume = last_settled["volume"] if last_settled else None
        daily_amount = last_settled["amount"] if last_settled else None

        # 2) 官方读数（上个账期结算值）与本账期累计用量
        base_reading = info.get("meter_reading") or 0.0
        base_date = info.get("meter_reading_date")
        include_base_day = bool(
            getattr(self.entry, "data", {}).get(CONF_INCLUDE_BASE_DAY, DEFAULT_INCLUDE_BASE_DAY)
        )
        cycle_total = None
        if base_date and settled_date and settled_date >= base_date:
            cycle_total = round(
                sum(
                    r["volume"] for r in settled
                    if (base_date <= r["date"] if include_base_day else base_date < r["date"])
                    and r["date"] <= settled_date
                ),
                4,
            )
        # 预计当前读数 = 官方读数 + 本账期累计用量（与官方口径对齐的连续估算）
        estimated_reading = round(base_reading + (cycle_total or 0.0), 4)

        # 3) 年度阶梯相关（泰能民用气按“户·年”累计，每年 1 月 1 日清零）
        #    官方年度已结算累计 = cycleCreditQty（每月抄表结算后更新，如 145.0）；
        #    抄表后仍未结算的增量取「与 cycle_total 相同的行过滤 + 仅限本年度」，
        #    避免跨年时把上一年的量误计入新年（默认账期从抄表次日算起）。
        year = date.fromisoformat(today).year
        cyc_end = info.get("cyc_end_date") or ""
        annual_settled: float | None = None
        credit = info.get("cycle_credit_qty")
        if credit is not None:
            cyc_year = year
            if len(cyc_end) >= 4 and cyc_end[:4].isdigit():
                cyc_year = int(cyc_end[:4])
            # 平台年度计费周期与本年度不一致（年初重置未完成等）时按 0 重新累计
            annual_settled = round(float(credit), 4) if cyc_year == year else 0.0
        annual_unbilled = 0.0
        if base_date and settled_date and settled_date >= base_date:
            annual_unbilled = round(
                sum(
                    r["volume"] for r in settled
                    if r["date"].startswith(str(year))
                    and (base_date <= r["date"] if include_base_day else base_date < r["date"])
                    and r["date"] <= settled_date
                ),
                4,
            )
        annual_usage: float | None = None
        if annual_settled is not None:
            annual_usage = round(annual_settled + annual_unbilled, 4)

        # 4) 最近 7 天明细
        recent = [
            {"date": r["date"], "volume": round(r["volume"], 4)}
            for r in settled[-RECENT_DAYS:]
        ]

        # 5) 全部已结算日的逐日用量（滚动窗口内，用于每日耗气量实体）
        daily_series = [
            {"date": r["date"], "volume": round(r["volume"], 4)}
            for r in settled
        ]

        # 6) 数据滞后天数
        lag = None
        if settled_date:
            lag = (date.fromisoformat(today) - date.fromisoformat(settled_date)).days

        return {
            "official_reading": base_reading,
            "estimated_reading": estimated_reading,
            "cycle_total": cycle_total,
            "cycle_start": base_date,
            "cycle_end": settled_date,
            "daily_usage": daily_volume,
            "daily_amount": daily_amount,
            "settled_date": settled_date,
            "data_lag_days": lag,
            "recent_days": recent,
            "daily_series": daily_series,
            "base_reading": base_reading,   # 兼容别名
            "base_date": base_date,
            # 年度阶梯（供「当年累计用量」「燃气费单价」传感器）
            "annual_settled": annual_settled,
            "annual_unbilled": annual_unbilled,
            "annual_usage": annual_usage,
            "annual_cycle_end": cyc_end or None,
            "annual_cycle_year": year,
            "annual_ladder": info.get("ladder"),
            "tier_remaining": _parse_tier_remaining(info.get("cyc_surplus")),
            "user_no": info.get("user_no", ""),
            "meter_no": info.get("meter_no", ""),
            "user_address": info.get("user_address", ""),
            "company_des": info.get("company_des", ""),
            "raw_count": len(rows),
        }

    # ------------------------------------------------------------------
    def masked_user_no(self) -> str:
        return _mask(self.user_info.get("user_no", ""))

    def masked_meter_no(self) -> str:
        return _mask(self.user_info.get("meter_no", ""))


def build_last_reset(settled_date: str | None) -> datetime | None:
    """把已结算日期转成当日零点，供 state_class=total 的传感器使用。"""
    if not settled_date:
        return None
    try:
        day = date.fromisoformat(settled_date)
    except ValueError:
        return None
    return datetime.combine(day, datetime.min.time())
