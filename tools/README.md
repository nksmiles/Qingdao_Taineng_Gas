# tools —— 本地测试 / 验证脚本

本目录存放**不依赖 Home Assistant** 的测试与验证脚本。
测试需要的 `SESSION / USER_NO / METER_NO / METER_TYPE` 均**在运行时由用户输入**，
脚本内没有任何预填凭据。


---

## 目录

| 文件 | 用途 | 是否需要联网 | 依赖 |
|---|---|---|---|
| `verify_session.py` | 验证 SESSION 是否有效、网络是否被网关拦截，并自动读出默认户号/表号/表读数 | ✅ 需要（必须家庭宽带/HA 局域网） | `requests` |
| `test_cumulative_reading.py` | 离线验证「累计表读数」算法是否符合验收标准 | ❌ 不需要 | 仅标准库 |
| `README.md` | 本测试说明 | — | — |

---

## 1. verify_session.py —— SESSION 有效性验证

**作用**：确认抓包得到的 SESSION 仍有效、当前网络能访问泰能网关，
并在结尾给出可直接填入 HA 集成的「户号 / 表号 / 表类型」。

**前提**：
- 在家庭宽带 / HA 所在局域网内运行（公司网络、云服务器、代理会被网关 403）。
- `pip3 install requests`

**运行**（交互式，脚本会逐项询问）：
```bash
python3 tools/verify_session.py
```

**询问项**：
| 输入项 | 说明 |
|---|---|
| `SESSION` | **必填**。抓包得到的 Cookie 值，形如 `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `户号` | 可留空 → 自动取公众号默认用户 |
| `表号` | 可留空 → 自动取默认用户的表 |
| `表类型` | 可留空 → 默认 `17` |

**判定方法**：
- 步骤 1 用真 SESSION 调 `getBindUserInfo` 成功 → SESSION 有效；
- 步骤 3 用全 0 的假 SESSION 调同一接口被拒 → 证明服务端确实校验 SESSION。

**常见结果**：
| 现象 | 含义 | 处理 |
|---|---|---|
| `网关拒绝（出口 IP 被拦截）` | 403 被网关拦 | 关闭代理、换回家庭宽带重试 |
| `业务失败 … responseCode=…` | SESSION 失效 | 在公众号里重新打开「用能分析」抓新包 |
| 网络异常/超时 | 本机网络不通 | 检查 DNS/防火墙 |

> 个别家庭网络存在代理/抓包工具证书劫持，如遇 TLS 校验失败可临时执行
> `VERIFY_SSL=0 python3 tools/verify_session.py`（PowerShell: `$env:VERIFY_SSL='0'`）。

---

## 2. test_cumulative_reading.py —— 累计表读数算法离线验证

**作用**：直接用仓库根目录的 HAR 抓包快照（2026-09-06 抓取）喂给
`custom_components/qingdao_taineng_gas/coordinator.py` 的真实计算函数，
核对结果是否符合提示词中的验收标准：

| 指标 | 期望值 |
|---|---|
| 抄表日之后增量 | 0.7+0.6+0.6+0.5+0.4+0.3 = **3.1 m³** |
| 累计表读数（默认） | 官方 207.0 @ 2026-08-30 + 3.1 = **210.1 m³** |
| 最近一日用量 | **0.3 m³**（2026-09-05） |
| 本月累计（9 月） | 0.6+0.6+0.5+0.4+0.3 = **2.4 m³**（8-31 属 8 月不计） |
| 数据滞后 | **1 天** |
| 校准开关打开后累计 | 多含 08-30 的 0.4 → **210.5 m³** |

**运行**（无需联网、无需装 HA，Python 3.9+）：
```bash
python3 tools/test_cumulative_reading.py                 # 默认档 + 校准档两种都测
python3 tools/test_cumulative_reading.py --include-base-day   # 语法校验用（已内置跑两种）
```

**它做了什么**：
1. 自动从 HAR 中定位 `getBindUserInfo` 与 `iotBarChart(type=month)` 的真实响应；
2. 以 HAR 文件名内嵌日期 `2026-09-06` 作为参考“今天”（保证离线可复现）；
3. 用一套与集成实现完全独立的“参考实现”做自洽对照；
4. 若参考日期是验收快照日，再与验收标准数值逐项核对。

**退出码**：0=全部通过；1=存在不一致；2=缺少 HAR/格式不对。

> 若你之后重新抓包换了 HAR（日期不同），脚本会提示跳过“验收”比对，
> 此时请只关注“对照参考实现”是否一致，并以公众号实际显示为准微调。

---

## 3. 常见问题

**Q：验证脚本会在联网时泄露数据吗？**
不会。请求只发给 `cloudselfhelp-mobile.eslink.cc`，本地仅打印脱敏信息与当前表读数。

**Q：SESSION 过期了怎么知道？**
运行 `verify_session.py`，若步骤 1 返回“业务失败”即为过期；
在公众号重新打开用能分析页面抓新包即可，HA 集成里“选项 → 更新 SESSION”可原地换。
