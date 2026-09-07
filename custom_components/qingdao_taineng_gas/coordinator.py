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

        累计表读数算法说明：
            官方表读数（meterReading）仅在每月抄表日更新，若直接暴露该值，
            两次抄表之间会有近 30 天数据纹丝不动，能源面板会出现长平台期。
            因此这里以「官方读数 + 抄表日之后所有已结算日用量之和」作为
            实时估算的累计读数，既连续又与官方口径对齐。
        """
        if not today:
            today = date.today().isoformat()
        current_month = today[:7]

        # 数据滞后一天：当天那条通常为 0（未结算），故排除当天
        settled = [r for r in rows if r["date"] < today]
        last_settled = settled[-1] if settled else None
        settled_date = last_settled["date"] if last_settled else None

        # 1) 最近一日用量（取已结算的最后一天）
        daily_volume = last_settled["volume"] if last_settled else None
        daily_amount = last_settled["amount"] if last_settled else None

        # 2) 累计表读数 = 官方读数 + 抄表日之后的已结算用量之和
        #    注意：官方读数是否已含抄表当天用量无法从接口判断，
        #    故提供 include_base_day 选项供校准（见 const.py 注释）。
        base_reading = info.get("meter_reading") or 0.0
        base_date = info.get("meter_reading_date")
        include_base_day = bool(
            getattr(self.entry, "data", {}).get(CONF_INCLUDE_BASE_DAY, DEFAULT_INCLUDE_BASE_DAY)
        )
        if base_date and settled_date:
            delta = sum(
                r["volume"] for r in settled
                if (base_date <= r["date"] if include_base_day else base_date < r["date"])
                and r["date"] <= settled_date
            )
        else:
            # 无官方抄表日期时，无法累加，仅用官方值
            delta = 0.0
        cumulative = round(base_reading + delta, 4)

        # 3) 本月累计（仅统计已结算日）
        month_total = round(
            sum(
                r["volume"] for r in settled
                if r["date"].startswith(current_month)
            ),
            4,
        )

        # 4) 最近 7 天明细
        recent = [
            {"date": r["date"], "volume": round(r["volume"], 4)}
            for r in settled[-RECENT_DAYS:]
        ]

        # 5) 数据滞后天数
        lag = None
        if settled_date:
            lag = (date.fromisoformat(today) - date.fromisoformat(settled_date)).days

        return {
            "cumulative_reading": cumulative,
            "daily_usage": daily_volume,
            "daily_amount": daily_amount,
            "month_total": month_total,
            "settled_date": settled_date,
            "data_lag_days": lag,
            "recent_days": recent,
            "base_reading": base_reading,
            "base_date": base_date,
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
