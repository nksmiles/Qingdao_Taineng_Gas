# -*- coding: utf-8 -*-
"""
泰能燃气（ESLink 易联云）SESSION 验证脚本（交互式）
=====================================================
用途：
  1. 验证你抓包得到的 SESSION Cookie 是否仍然有效
  2. 顺带测试当前网络是否被网关拦截（家庭宽带才能过）
  3. 自动读取默认户号/表号/当前表读数，方便你填入 HA 集成
  4. 用「假 SESSION」做一次对照，证明 SESSION 确实被服务端校验

本脚本不预设任何凭据。运行时会逐项询问：
  SESSION    —— 必填，抓包得到的 Cookie 值
  户号/表号  —— 可留空，留空会自动取公众号里的默认用户
  表类型     —— 可留空，默认 17（民用 NB-IoT 表）

运行前提：必须在家庭宽带 / HA 所在局域网内运行。
          云服务器、公司网络、代理环境大概率被网关 403 拒绝。

用法：
  python3 tools/verify_session.py

需要先安装依赖（仅本脚本需要）：
  pip3 install requests
"""
from __future__ import annotations

import datetime
import json
import os
import sys

try:
    import requests
except ImportError:
    print("缺少 requests 库，请先执行： pip3 install requests")
    raise SystemExit(1)

requests.packages.urllib3.disable_warnings()

# 是否校验 TLS 证书：默认校验；个别家庭网络有代理劫持时置 0
VERIFY_SSL = os.environ.get("VERIFY_SSL", "1") == "1"

HOST = "cloudselfhelp-mobile.eslink.cc"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0xf2541c1a)"
)


def line(t: str = "") -> None:
    print(t)


def head(t: str) -> None:
    line()
    line("=" * 66)
    line(t)
    line("=" * 66)


def mask(value: str) -> str:
    """户号/表号脱敏展示（本地验证也避免满屏敏感信息）。"""
    value = str(value or "")
    if len(value) <= 4:
        return value
    return f"*{value[-4:]}"


def post(path: str, data: dict, session_cookie: str) -> tuple[int | None, str, str]:
    """发起一次表单 POST，返回 (HTTP状态码, 响应文本, 异常说明)。"""
    url = f"https://{HOST}{path}"
    headers = {
        "oAuthType": "AUTH_MOBILE",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        # 与集成 api.py 一致：Origin/Referer 用 http 方案，https 请求同样可用（已实测）
        "Origin": f"http://{HOST}",
        "Referer": f"http://{HOST}/?showyqje=0",
    }
    try:
        r = requests.post(
            url,
            headers=headers,
            data=data,
            cookies={"SESSION": session_cookie},
            timeout=25,
            verify=VERIFY_SSL,
            allow_redirects=False,
        )
        return r.status_code, r.text, ""
    except requests.exceptions.SSLError:
        return None, "", "TLS 证书校验失败，可设环境变量 VERIFY_SSL=0 后重试"
    except Exception as ex:  # noqa: BLE001
        return None, "", f"{type(ex).__name__}: {str(ex)[:150]}"


def judge(code: int | None, text: str) -> tuple[bool, str]:
    """判断一次请求在业务层面是否成功。"""
    if code is None:
        return False, "请求异常"
    if code == 403 and "policy_default_denied" in text:
        return False, "网关拒绝（出口 IP 被拦截，请确认在家庭宽带）"
    if code != 200:
        return False, f"HTTP {code}"
    if '"responseCode":"100000"' not in text:
        try:
            j = json.loads(text)
            return False, f"业务失败: {j.get('message', '')} / responseCode={j.get('responseCode', '')}"
        except Exception:  # noqa: BLE001
            return False, f"业务失败: {text[:120]}"
    return True, "成功"


def ask(desc: str, required: bool = False, default: str = "") -> str:
    """向用户询问一个值；required=True 时不允许留空。"""
    if required:
        tip = "（必填）"
    elif default:
        tip = f"（默认 {default}）"
    else:
        tip = "（留空自动）"
    while True:
        value = input(f"  请输入 {desc} {tip}: ").strip()
        if required and not value:
            print("  ⚠️ 该项不能为空，请重新输入。")
            continue
        return value


def pick_user(users: list[dict], want_user: str, want_meter: str) -> dict | None:
    """按户号/表号筛选；都不指定时取默认用户（defaultUser=1）或第一个。"""
    if not want_user and not want_meter:
        for u in users:
            if u.get("defaultUser") == 1:
                return u
        return users[0]
    for u in users:
        if want_user and str(u.get("userNo")) != want_user:
            continue
        if want_meter and str(u.get("meterNo")) != want_meter:
            continue
        return u
    return None


def main() -> int:
    line()
    head("青岛泰能燃气 SESSION 验证脚本")
    line("※ 必须在家庭宽带 / HA 局域网内运行；公司网络与云服务器会被网关拦截。")
    line("※ 输入内容仅本次进程使用，不会写入任何文件。")
    line()

    session_cookie = ask("SESSION Cookie", required=True)
    want_user = ask("户号")
    want_meter = ask("表号")
    want_type = ask("表类型", default="17")

    # ----------------------------------------------------------
    head("步骤 1：查询绑定用户 / 户号 / 表号 / 当前表读数")
    code, text, err = post(
        "/utility/userBind/getBindUserInfo",
        {"includeStoppedMeter": "true", "token": ""},
        session_cookie,
    )
    ok, msg = judge(code, text)
    line(f"  HTTP {code} —— {msg}")
    if err:
        line(f"  ⚠️ {err}")
    if not ok:
        line()
        line("  ┌────────────────────────────────────────────────────────┐")
        line("  │  无法继续。请确认：                                    │")
        line("  │  1) 当前在家庭宽带/HA 局域网                          │")
        line("  │  2) SESSION 未过期（过期需在公众号重新抓包）           │")
        line("  └────────────────────────────────────────────────────────┘")
        return 1

    payload = json.loads(text)
    user_list = payload["result"].get("userBindList") or []
    if not user_list:
        line("  ❌ 该 SESSION 下没有绑定任何燃气表具。")
        return 1

    user = pick_user(user_list, want_user, want_meter)
    if user is None:
        line(f"  ❌ 未找到户号={want_user or '(任意)'} / 表号={want_meter or '(任意)'} 的表具。")
        line(f"     可选户号：{', '.join(mask(str(u.get('userNo'))) for u in user_list)}")
        return 1

    meter = (user.get("meterIds") or [{}])[0]
    user_no = str(user.get("userNo"))
    meter_no = str(user.get("meterNo"))
    meter_type = str(user.get("meterType") or meter.get("type") or "17")
    if want_type not in ("", "17"):
        meter_type = want_type
    reading = meter.get("meterReading")
    reading_date = meter.get("meterReadingDate")

    line(f"  ✅ 户号 = {user_no}  （脱敏 {mask(user_no)}）")
    line(f"     表号 = {meter_no}  （脱敏 {mask(meter_no)}）")
    line(f"     表类型 = {meter_type}")
    line(f"     当前表读数 = {reading} m³   抄表日期 = {reading_date}")
    line(f"     地址 = {user.get('userAddress', '')}   类型 = {user.get('userTypeDes', '')}")
    if user.get("defaultUser") != 1:
        line("     ⚠️ 这不是公众号默认用户，若绑定了多个户号请在 HA 中填写户号/表号。")

    # ----------------------------------------------------------
    head("步骤 2：查询日用量（验证用量接口可用）")
    month = datetime.date.today().strftime("%Y-%m")
    code, text, err = post(
        "/fee/chart/iotBarChart",
        {
            "userNo": user_no,
            "meterNo": meter_no,
            "type": "month",
            "meterType": meter_type,
            "startTime": "",
            "endTime": "",
            "time": month,
        },
        session_cookie,
    )
    ok, msg = judge(code, text)
    line(f"  HTTP {code} —— {msg}（请求 time={month}）")
    if err:
        line(f"  ⚠️ {err}")
    if ok:
        detail = json.loads(text)["result"]["usageDetail"]
        line(f"  ✅ 返回 {len(detail)} 条日用量：{detail[0]['readingTime']} ~ {detail[-1]['readingTime']}")
        line(f"     末条（多为当天未结算）: 日期={detail[-1]['readingTime']} "
             f"用量={detail[-1]['cycleTotalVolume']} m³")
    else:
        line("  ❌ 用量接口失败，见上方原因。")

    # ----------------------------------------------------------
    head("步骤 3：假 SESSION 对照（证明 SESSION 确实被校验）")
    code, text, _ = post(
        "/utility/userBind/getBindUserInfo",
        {"includeStoppedMeter": "true", "token": ""},
        "00000000-0000-0000-0000-000000000000",
    )
    _, msg = judge(code, text)
    line(f"  用假 SESSION → HTTP {code} —— {msg}")
    if '"responseCode":"100000"' in (text or ""):
        line("  ⚠️ 假 SESSION 竟然成功 → 该接口可能不校验 SESSION，请留意")
    else:
        line("  ✅ 假 SESSION 被拒而真 SESSION 通过 → SESSION 有效且被校验")

    # ----------------------------------------------------------
    head("✅ 验证结论")
    line("  SESSION 有效，网络未被拦截。请在 HA 集成中使用以下值：")
    line()
    line(f"    SESSION   = {session_cookie}")
    line(f"    户号      = {user_no}")
    line(f"    表号      = {meter_no}")
    line(f"    表类型    = {meter_type}")
    line()
    line("  提示：SESSION 会过期。若日后 HA 提示重新认证，按上面步骤重新抓包即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
