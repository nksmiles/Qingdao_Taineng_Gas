# 高级使用与诊断指南

> 本页整理 **README 之外的进阶内容**，适用于：
> - 想要**离线验证 / 复现**集成算法（HAR 快照 + 参考实现对照）
> - 电脑端抓包受阻，需要**手机抓包**补充方案
> - 排查 SESSION 有效性、网关拦截等**诊断**场景
> - 对接口进行**二次开发**
>
> 前置条件：已按 README「获取 SESSION」完成基本抓包，并已能正常接入 HA。
> 日常使用只读 [README.md](README.md) 即可。

---

## 目录

1. [导出 HAR 快照（离线测试）](#1-导出-har-快照离线测试)
2. [手机抓包（电脑微信抓不到时，未测试）](#2-手机抓包电脑微信抓不到时未测试)
3. [SESSION 有效性验证](#3-session-有效性验证)
4. [附带工具](#4-附带工具)
5. [接口说明（供二次开发）](#5-接口说明供二次开发)

---

## 1. 导出 HAR 快照（离线测试）

`tools/test_cumulative_reading.py` 会自动读取仓库根目录的 HAR 快照做算法验证，
重新抓包后可顺手导出一份更新：

1. 确认已抓到 README 教程中第 4 步的两条请求
   （`getBindUserInfo` 与 `type=month` 的 `iotBarChart`）
2. 在 Reqable 中选中这两条请求 → 右键 → **导出 → HAR**
3. 保存到本仓库**根目录**，文件名以 `channel-` 开头、以 `.har.json` 结尾即可被自动识别，
   例如 `channel-wechatserver.eslink.cc_2026_09_06.har.json`

   > ⚠️ HAR 内含真实户号 / 表号 / 地址等隐私，已被 `.gitignore` 排除，**切勿上传 GitHub**。

4. 运行校验（无需联网、无需装 HA）：

   ```bash
   python3 tools/test_cumulative_reading.py
   ```

   退出码含义：`0`=全部通过；`1`=存在不一致；`2`=缺少 HAR / 格式不对。

> HAR 文件名内嵌的日期会被当作参考"今天"，保证结果离线可复现。
> 若重新抓包后日期变化，脚本会跳过"验收"数值比对，只做与参考实现的自洽对照，
> 此时以公众号实际显示为准即可。

---

## 2. 手机抓包（电脑微信抓不到时，未测试）

> 电脑微信抓不到时，可用手机走代理抓包。**本方案未实测**，仅作补充备选。

1. 手机与电脑连接**同一 WiFi**
2. Reqable 首页 / 设置里查看本机代理地址，形如 `192.168.x.x:9000`
3. 手机 WiFi 设置 → 代理 → 手动，填上述 `IP:端口`
4. 手机浏览器访问 Reqable 提示的证书地址，下载并**安装、信任根证书**
5. 手机微信打开燃气用能分析页面，回到 Reqable 按 README 第 4 步的方法查找 `SESSION`
6. 用完记得把手机代理关掉

---

## 3. SESSION 有效性验证

填入 HA 之前，可先跑一下 `tools/verify_session.py`（见下文「附带工具」）确认 SESSION 有效。

诊断用途：

| 现象 | 含义 | 处理 |
|---|---|---|
| `网关拒绝（出口 IP 被拦截）` | 403 被网关拦 | 关闭代理、换回家庭宽带重试 |
| `业务失败 … responseCode=…` | SESSION 失效 | 在公众号里重新打开「用能分析」抓新包 |
| 网络异常 / 超时 | 本机网络不通 | 检查 DNS / 防火墙 |

> 个别家庭网络存在代理 / 抓包工具证书劫持，如遇 TLS 校验失败可临时执行
> `VERIFY_SSL=0 python3 tools/verify_session.py`（PowerShell：`$env:VERIFY_SSL='0'`）。

---

## 4. 附带工具

> SESSION / 户号 / 表号 / 表类型等凭据均**在运行时由用户输入**，脚本内无任何预填值。

| 文件 | 用途 | 联网 |
|---|---|---|
| `tools/verify_session.py` | 交互式验证 SESSION 有效性、是否被网关拦截，并自动读出默认户号/表号/表读数 | 需要 |
| `tools/test_cumulative_reading.py` | 离线算法验证（读取 HAR 快照），不依赖 HA 环境 | 不需要 |
| `tools/README.md` | 工具详细使用说明与验收对照表 | — |

运行验证脚本（需家庭宽带 / HA 局域网）：

```bash
pip3 install requests
python3 tools/verify_session.py
```

运行离线算法验证（无需联网）：

```bash
python3 tools/test_cumulative_reading.py
```

---

## 5. 接口说明（供二次开发）

详见 `custom_components/qingdao_taineng_gas/api.py`，要点：

- **无签名、无 nonce、无 AES 加密**，纯表单 POST
- 请求头固定带 `oAuthType: AUTH_MOBILE`
- 凭据 = `Cookie: SESSION=xxx`，`acw_tc`（WAF Cookie）**实测非必需**
- 外层状态 `responseCode == "100000"`，内层看 `result.success`

| 接口 | 用途 |
|---|---|
| `POST /utility/userBind/getBindUserInfo` | 户号、表号、官方读数 |
| `POST /fee/chart/iotBarChart` | 用量（`type=month` 日粒度 / `type=year` 月粒度） |

### 5.1 ⚠️ 滚动窗口陷阱

该接口的 `time` 参数**不代表自然月**：

```
请求 time=2026-09 → 返回 2026-08-01 ~ 2026-09-06（37 条）
请求 time=2026-08 → 返回 2026-08-01 ~ 2026-09-01（32 条）
```

两次**起点相同**（今天往前推 36 天），只有终点不同。
所以**永远传当前月，然后取 `readingTime` 最大的一条**。

### 5.2 需避开的接口

`/utility/en/rechargePayment/queryMeterInfo` 的请求与响应**均为 AES 密文**，
无法复现，**不要使用**。所需数据已由 `getBindUserInfo` 提供。
