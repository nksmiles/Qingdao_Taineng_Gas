"""青岛泰能燃气（ESLink 易联云）Home Assistant 集成。

通过微信公众号「泰能天然气有限公司」背后的 ESLink 易联云平台，
读取每日用气量与燃气表读数。

凭据说明：
    code 只能由微信客户端在 OAuth 跳转时产生，服务端无法自动获取，
    因此本集成不做自动登录 —— 用户需抓包获取 SESSION Cookie 后填入。
    SESSION 过期时，HA 会弹出重新认证流程，在选项里更新即可，无需删除集成。
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import TanengGasCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """通过配置条目设置集成。"""
    coordinator = TanengGasCoordinator(hass, entry)
    # v1.4.3：先从磁盘 Store 恢复每日用量历史，再发起首轮 API 刷新，
    # 否则超 ESLink 滚动窗口（约 37 天）的 8 月份「每日耗气量」实体
    # 会因为首次 ``_async_update_data`` 拿不到该日期而 ``unavailable``。
    await coordinator.async_load_daily_history()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 首轮刷新完成后启用每日查询计划（基础时间 + 每天次数自动排程）
    coordinator.start_query_schedule()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载配置条目。"""
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is not None:
        await coordinator.async_shutdown()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """重新加载配置条目（更新 SESSION 后触发）。"""
    await hass.config_entries.async_reload(entry.entry_id)
