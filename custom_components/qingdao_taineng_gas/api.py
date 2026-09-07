"""ESLink 易联云接口客户端（青岛泰能燃气）。

接口特征（均为抓包实测，勿凭猜测修改）：
  * 无签名、无 nonce、无 timestamp、请求体为纯表单
  * 真正的持久凭据是 SESSION Cookie，token 仅用于首次换取会话
  * acw_tc（WAF Cookie）实测非必需，本客户端不主动携带
  * 外层状态看 responseCode，内层状态看 result.success / echoCode
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any

import aiohttp

from .const import (
    BASE_URL,
    CONTENT_TYPE_FORM,
    DEFAULT_METER_TYPE,
    DEFAULT_TIMEOUT,
    HEADER_OAUTH_TYPE,
    ORIGIN,
    PATH_BIND_USER,
    PATH_CHART,
    REFERER,
    RESPONSE_CODE_OK,
)

_LOGGER = logging.getLogger(__name__)


class EsLinkAuthError(Exception):
    """SESSION 无效或已过期，需要用户重新抓包。"""


class EsLinkError(Exception):
    """网络异常或接口返回非预期内容。"""


def _to_float(value: Any) -> float:
    """把接口返回的字符串金额/用量转成 float。

    实测同一字段会出现 "0.0000" 与 "0" 两种写法，需统一兼容；
    另有个别字段可能直接是数字类型。
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        _LOGGER.debug("无法转换为数字：%s", text)
        return 0.0


def _normalize_date(yyyymmdd: str | None) -> str | None:
    """把 20260830 形式的抄表日期转成 2026-08-30，便于字符串直接比较。"""
    if not yyyymmdd:
        return None
    text = str(yyyymmdd).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text


class EsLinkApi:
    """ESLink 易联云接口客户端。"""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        cookie_session: str,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self._session = session
        self._cookie_session = cookie_session
        self._timeout = timeout

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @property
    def _headers(self) -> dict[str, str]:
        return {
            "oAuthType": HEADER_OAUTH_TYPE,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": CONTENT_TYPE_FORM,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": ORIGIN,
            "Referer": REFERER,
        }

    @property
    def _cookies(self) -> dict[str, str]:
        # 只带 SESSION：实测不带 acw_tc 亦可正常调用
        return {"SESSION": self._cookie_session}

    async def _post(self, path: str, data: dict[str, Any]) -> Any:
        """发起一次表单 POST，返回已解析的 result 字段。"""
        url = BASE_URL + path
        try:
            async with self._session.post(
                url,
                headers=self._headers,
                cookies=self._cookies,
                data=data,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as resp:
                status = resp.status
                text = await resp.text()
        except asyncio.TimeoutError as err:
            raise EsLinkError("请求超时，请检查网络") from err
        except aiohttp.ClientError as err:
            raise EsLinkError(f"网络请求失败：{err}") from err

        if status == 403 or "policy_default_denied" in text:
            raise EsLinkError(
                "网关拒绝访问（403）。本接口仅允许家庭宽带出口访问，"
                "云服务器、公司网络、代理环境会被拦截"
            )
        if status != 200:
            raise EsLinkError(f"服务返回 HTTP {status}")

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as err:
            raise EsLinkError(f"响应不是合法 JSON：{text[:100]}") from err

        code = str(payload.get("responseCode", ""))
        if code != RESPONSE_CODE_OK:
            message = payload.get("message") or ""
            # 实测：SESSION 失效时 HTTP 仍为 200，仅 responseCode 变化
            raise EsLinkAuthError(f"{message}（responseCode={code}）")

        return payload.get("result")

    # ------------------------------------------------------------------
    # 业务方法
    # ------------------------------------------------------------------
    async def get_bind_user_info(self) -> list[dict[str, Any]]:
        """查询已绑定的用户、户号、表号与官方表读数。

        body: includeStoppedMeter=true&token=
        注意 token 传空值即可 —— SESSION Cookie 才是真正凭据。
        """
        result = await self._post(
            PATH_BIND_USER, {"includeStoppedMeter": "true", "token": ""}
        )
        if not isinstance(result, dict):
            raise EsLinkError("绑定信息响应格式异常")

        users: list[dict[str, Any]] = []
        for item in result.get("userBindList") or []:
            meters = item.get("meterIds") or []
            if not meters:
                continue
            meter = meters[0]
            users.append(
                {
                    "user_no": str(item.get("userNo") or ""),
                    "meter_no": str(item.get("meterNo") or meter.get("meterId") or ""),
                    "meter_type": str(item.get("meterType") or meter.get("type") or DEFAULT_METER_TYPE),
                    "user_name": item.get("userName") or "",
                    "user_address": item.get("userAddress") or "",
                    "user_type_des": item.get("userTypeDes") or "",
                    "company_des": item.get("companyDes") or "",
                    "org_no": item.get("orgNo") or "",
                    "meter_reading": _to_float(meter.get("meterReading")),
                    "meter_reading_date": _normalize_date(meter.get("meterReadingDate")),
                    "last_billing_date": _normalize_date(meter.get("lastBillingDate")),
                    "price1": _to_float(meter.get("price1")),
                    "cyc_surplus": meter.get("cycSurplus") or "",
                    "is_default": bool(item.get("defaultUser") == 1),
                    "is_active": bool(item.get("active")),
                }
            )
        if not users:
            raise EsLinkError("未查询到任何绑定的燃气表具")
        return users

    async def get_daily_usage(
        self,
        user_no: str,
        meter_no: str,
        meter_type: str = DEFAULT_METER_TYPE,
        month: str | None = None,
    ) -> list[dict[str, Any]]:
        """查询日粒度用量。

        :param month: YYYY-MM，默认当前月。

        重要：实测该接口返回的是「滚动窗口」而非自然月（起点 ≈ 今天往前 37 天），
        窗口与 time 参数月份关系不大，因此**永远传当前月**。
        响应包含当天那条（volume=0，未结算），由协调器按 date < 今天 过滤，
        逐日数据可直接获取（tools/test_cumulative_reading.py --online 可在线核对）。
        """
        if month is None:
            month = date.today().strftime("%Y-%m")

        result = await self._post(
            PATH_CHART,
            {
                "userNo": user_no,
                "meterNo": meter_no,
                "type": "month",
                "meterType": meter_type,
                "startTime": "",
                "endTime": "",
                "time": month,
            },
        )
        if not isinstance(result, dict):
            raise EsLinkError("用量响应格式异常")

        rows: list[dict[str, Any]] = []
        for item in result.get("usageDetail") or []:
            day = item.get("readingTime")
            if not day:
                continue
            rows.append(
                {
                    "date": str(day),
                    "volume": _to_float(item.get("cycleTotalVolume")),  # 用气量 m³
                    "amount": _to_float(item.get("cycleTotalValues")),  # 金额，实测多为 0
                }
            )
        rows.sort(key=lambda r: r["date"])
        return rows

    async def get_monthly_usage(
        self,
        user_no: str,
        meter_no: str,
        meter_type: str = DEFAULT_METER_TYPE,
        year: str | None = None,
    ) -> list[dict[str, Any]]:
        """查询月粒度用量（type=year，返回逐月汇总）。"""
        if year is None:
            year = date.today().strftime("%Y")

        result = await self._post(
            PATH_CHART,
            {
                "userNo": user_no,
                "meterNo": meter_no,
                "type": "year",
                "meterType": meter_type,
                "startTime": "",
                "endTime": "",
                "time": year,
            },
        )
        if not isinstance(result, dict):
            raise EsLinkError("年度用量响应格式异常")

        rows: list[dict[str, Any]] = []
        for item in result.get("usageDetail") or []:
            month_key = item.get("readingTime")
            if not month_key:
                continue
            rows.append(
                {
                    "month": str(month_key),
                    "volume": _to_float(item.get("cycleTotalVolume")),
                    "amount": _to_float(item.get("cycleTotalValues")),
                    "label": item.get("useGasMonth") or "",
                }
            )
        rows.sort(key=lambda r: r["month"])
        return rows
