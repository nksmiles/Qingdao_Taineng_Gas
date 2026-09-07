"""配置流程 —— 支持 UI 配置、重新认证（更新 SESSION）与选项更新。"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EsLinkApi, EsLinkAuthError, EsLinkError
from .const import (
    CONF_BASE_QUERY_TIME,
    CONF_INCLUDE_BASE_DAY,
    CONF_METER_NO,
    CONF_METER_TYPE,
    CONF_QUERY_COUNT,
    CONF_SESSION,
    CONF_USER_NO,
    DEFAULT_BASE_QUERY_TIME,
    DEFAULT_INCLUDE_BASE_DAY,
    DEFAULT_METER_TYPE,
    DEFAULT_QUERY_COUNT,
    DOMAIN,
    QUERY_COUNT_MAX,
    QUERY_COUNT_MIN,
    parse_query_time,
    query_times_for,
)

_LOGGER = logging.getLogger(__name__)


def _build_schema(defaults: dict[str, Any] | None = None, require_session: bool = True) -> vol.Schema:
    """构造配置表单。"""
    defaults = defaults or {}
    session_key = vol.Required if require_session else vol.Optional
    return vol.Schema(
        {
            session_key(CONF_SESSION, default=defaults.get(CONF_SESSION, "")): str,
            vol.Optional(CONF_USER_NO, default=defaults.get(CONF_USER_NO, "")): str,
            vol.Optional(CONF_METER_NO, default=defaults.get(CONF_METER_NO, "")): str,
            vol.Optional(
                CONF_METER_TYPE, default=defaults.get(CONF_METER_TYPE, DEFAULT_METER_TYPE)
            ): str,
        }
    )


async def _validate_session(hass, cookie_session: str) -> list[dict[str, Any]]:
    """校验 SESSION 是否有效，返回绑定用户列表。"""
    api = EsLinkApi(async_get_clientsession(hass), cookie_session)
    return await api.get_bind_user_info()


def _normalize_query_count(value: Any) -> int | None:
    """把「每天查询次数」规范化为 1~4 的整数；非法返回 None。"""
    if value in (None, ""):
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if QUERY_COUNT_MIN <= count <= QUERY_COUNT_MAX else None


def _normalize_query_time(value: Any) -> str | None:
    """把「基础查询时间」规范化为 HH:MM；非法返回 None。"""
    minutes = parse_query_time(value)
    if minutes is None:
        return None
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class TanengGasConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """泰能燃气配置流程。"""

    VERSION = 1

    def __init__(self) -> None:
        self._users: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """用户手动添加集成。"""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                users = await _validate_session(self.hass, user_input[CONF_SESSION])
            except EsLinkAuthError as err:
                _LOGGER.debug("SESSION 校验失败：%s", err)
                errors["base"] = "invalid_auth"
            except EsLinkError as err:
                _LOGGER.debug("连接失败：%s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("添加集成时发生未知错误")
                errors["base"] = "unknown"
            else:
                self._users = users

                user_no = (user_input.get(CONF_USER_NO) or "").strip()
                meter_no = (user_input.get(CONF_METER_NO) or "").strip()

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
                        errors["base"] = "meter_not_found"
                        return self.async_show_form(
                            step_id="user",
                            data_schema=_build_schema(user_input),
                            errors=errors,
                        )

                if info is None:
                    # 留空则自动取默认用户
                    info = next((u for u in users if u["is_default"]), users[0])

                await self.async_set_unique_id(f"{info['user_no']}_{info['meter_no']}")
                self._abort_if_unique_id_configured()

                title = f"泰能燃气 {info['meter_no'][-4:]}"
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_SESSION: user_input[CONF_SESSION],
                        CONF_USER_NO: info["user_no"],
                        CONF_METER_NO: info["meter_no"],
                        CONF_METER_TYPE: info["meter_type"] or DEFAULT_METER_TYPE,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=_build_schema(), errors=errors
        )

    # ------------------------------------------------------------------
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """SESSION 失效时由协调器触发。"""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """重新输入 SESSION。"""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])

        if user_input is not None:
            try:
                await _validate_session(self.hass, user_input[CONF_SESSION])
            except EsLinkAuthError:
                errors["base"] = "invalid_auth"
            except EsLinkError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("重新认证时发生未知错误")
                errors["base"] = "unknown"
            else:
                if entry:
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_SESSION: user_input[CONF_SESSION]}
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_SESSION): str}),
            errors=errors,
            description_placeholders={},
        )

    # ------------------------------------------------------------------
    @staticmethod
    def async_get_options_flow(entry: config_entries.ConfigEntry) -> "TanengGasOptionsFlow":
        """返回选项流程。"""
        return TanengGasOptionsFlow(entry)


class TanengGasOptionsFlow(config_entries.OptionsFlow):
    """选项流程 —— SESSION 过期后可在此直接更新，无需删除集成。"""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    @property
    def _target_entry(self) -> config_entries.ConfigEntry:
        # 新版 HA 通过 self.config_entry 提供，旧版回退到构造参数
        return getattr(self, "config_entry", None) or self._entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """选项主步骤。"""
        errors: dict[str, str] = {}
        entry = self._target_entry

        if user_input is not None:
            new_data = dict(entry.data)
            new_session = (user_input.get(CONF_SESSION) or "").strip()

            if new_session:
                try:
                    await _validate_session(self.hass, new_session)
                except EsLinkAuthError:
                    errors["base"] = "invalid_auth"
                except EsLinkError:
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("更新选项时发生未知错误")
                    errors["base"] = "unknown"
                else:
                    new_data[CONF_SESSION] = new_session

            if not errors:
                meter_type = (user_input.get(CONF_METER_TYPE) or "").strip()
                if meter_type:
                    new_data[CONF_METER_TYPE] = meter_type

                new_data[CONF_INCLUDE_BASE_DAY] = bool(
                    user_input.get(CONF_INCLUDE_BASE_DAY, DEFAULT_INCLUDE_BASE_DAY)
                )

                # 每日查询计划：每天查询次数（1~4）+ 基础查询时间
                query_count = _normalize_query_count(user_input.get(CONF_QUERY_COUNT))
                if query_count is None:
                    errors[CONF_QUERY_COUNT] = "invalid_query_count"
                else:
                    new_data[CONF_QUERY_COUNT] = query_count

                base_query_time = _normalize_query_time(
                    user_input.get(CONF_BASE_QUERY_TIME, DEFAULT_BASE_QUERY_TIME)
                )
                if base_query_time is None:
                    errors[CONF_BASE_QUERY_TIME] = "invalid_time"
                else:
                    new_data[CONF_BASE_QUERY_TIME] = base_query_time

            if not errors:
                self.hass.config_entries.async_update_entry(entry, data=new_data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_create_entry(title="", data={})

        query_count = _normalize_query_count(
            entry.data.get(CONF_QUERY_COUNT, DEFAULT_QUERY_COUNT)
        )
        base_query_time = _normalize_query_time(
            entry.data.get(CONF_BASE_QUERY_TIME, DEFAULT_BASE_QUERY_TIME)
        ) or DEFAULT_BASE_QUERY_TIME
        derived = query_times_for(base_query_time, query_count or DEFAULT_QUERY_COUNT)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_SESSION, default=""): str,
                    vol.Optional(
                        CONF_METER_TYPE,
                        default=entry.data.get(CONF_METER_TYPE, DEFAULT_METER_TYPE),
                    ): str,
                    vol.Optional(
                        CONF_INCLUDE_BASE_DAY,
                        default=entry.data.get(
                            CONF_INCLUDE_BASE_DAY, DEFAULT_INCLUDE_BASE_DAY
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_QUERY_COUNT,
                        default=query_count or DEFAULT_QUERY_COUNT,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=QUERY_COUNT_MIN,
                            max=QUERY_COUNT_MAX,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_BASE_QUERY_TIME,
                        default=base_query_time,
                    ): selector.TimeSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "derived_times": "、".join(derived),
            },
        )
