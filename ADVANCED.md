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
5. [传感器与账期口径对照](#5-传感器与账期口径对照)
6. [接口说明（供二次开发）](#6-接口说明供二次开发)
7. [抓包原始报文参考（脱敏）](#7-抓包原始报文参考脱敏)

---

## 1. 导出 HAR 快照（离线测试）

`tools/test_cumulative_reading.py` 会自动读取仓库根目录的所有
`channel-*.har.json` 快照，验证「上个账期累计表读数 / 本账期累计用量 /
每日耗气量 / 当年累计用量 / 燃气费单价」口径，并按账单核对**抄表间隔与阶梯用量**。
重新抓包后可顺手导出一份：

1. 在公众号「服务大厅 / 用能分析」停留片刻，确保抓到以下响应：
   - `getBindUserInfo`（官方读数 + 抄表日）
   - `iotBarChart`（`type=month` 日用量；有 `type=year` 更佳）
   - `getBillInfoListPage`（账单，用于阶梯核对，**可选**）
2. 在 Reqable 中选中这些请求 → 右键 → **导出 → HAR**
3. 保存到本仓库**根目录**，文件名以 `channel-` 开头、以 `.har.json` 结尾即可被自动识别，
   例如 `channel-wechatserver.eslink.cc_2026_09_07.har.json`

   > ⚠️ HAR 内含真实户号 / 表号 / 地址等隐私，已被 `.gitignore` 排除，**切勿上传 GitHub**。

4. 运行校验（无需联网、无需装 HA）：

   ```bash
   python3 tools/test_cumulative_reading.py            # 离线：默认档 + 校准档
   python3 tools/test_cumulative_reading.py --online   # 追加：用 HAR 内 SESSION 在线重拉核对
   ```

   退出码含义：`0`=全部通过；`1`=存在不一致；`2`=缺少 HAR / 格式不对。

> HAR 文件名内嵌的日期会被当作参考"今天"，保证结果离线可复现。
> 快照日期匹配已知验收值（`2026-09-06` / `2026-09-07`）时做逐项静态比对；
> 换了新日期的 HAR 则只做与参考实现的自洽对照，此时以公众号实际显示为准。
> `--online` 会真实请求网关（需家庭宽带 / HA 局域网），逐日数据与快照逐条比对，
> 用来确认该 SESSION 仍可直接获取全部日用量数据。

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
| `tools/test_cumulative_reading.py` | 离线算法验证（读取 HAR 快照）+ 抄表间隔/阶梯核对；`--online` 可在线重拉核对逐日数据 | `--online` 时 |
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

## 5. 传感器与账期口径对照

泰能每月在**抄表日**结算上一账期，账单周期 = 两次抄表日之间（非自然月）：

| 传感器 | 数值口径 | 更新节奏 |
|---|---|---|
| `上个账期累计表读数` | 官方读数（`getBindUserInfo.meterReading`），即上个账期结算累计 | 每月抄表时 |
| `本账期累计用量` | 官方抄表日之后、已结算日的用量之和 | 每天（新结算日出现时） |
| `最近一日用量` | 最后一个已结算日的用量 | 每天 |
| `每日耗气量 YYYY-MM-DD` | 单个已结算日的用量，值固定 | 结算当天新增一次 |
| `燃气总用量` | = 上个账期累计表读数 + 本账期累计用量（燃气表当前累计读数） | 每天 |
| `当年累计用量` | = 官方年度已结算累计（`cycleCreditQty`）+ 本年度未结算增量 | 每天 |
| `燃气费单价` | 按当年累计用量判定的当前边际单价（3.54 / 4.12 / 4.99 元/m³） | 每天 |

- 官方读数是否含抄表当天用量无法从接口判断，由集成选项
  `include_base_day` 决定本账期从抄表日当天（True）还是次日（False）起算。
- 能源面板：直接使用 **`燃气总用量`** 实体（`total_increasing`、跨账期跨年不归零），
  无需再建模板传感器；`上个账期累计表读数` 的属性 `estimated_reading` 与之同值。
- 阶梯计费按**户·年**累计（每年 1 月 1 日清零）：`当年累计用量`
  = 官方年度已结算累计（`getBindUserInfo.cycleCreditQty`，每月抄表结算后更新）
  + 本年度内抄表后仍未结算的用量（随逐日结算累加）；
  `燃气费单价`（边际价）按当年累计落在哪一档取 3.54 / 4.12 / 4.99 元/m³，
  跨过 228 / 348 m³ 阈值后自动切换。档位与阈值详情见 README「燃气费单价」小节。
- `每日耗气量 YYYY-MM-DD` 实体随每天结算自动新增（当日 0 值未结算不生成）。
  平台只保留约 37 天滚动窗口，HA 重启后仅重建**当前窗口内**的实体；
  更早日期无法再从接口取回，如需长期留存请用 `recent_days` 等属性自行存档。
  搜索「每日耗气量」可查看全部该系列实体。

---

## 6. 接口说明（供二次开发）

详见 `custom_components/qingdao_taineng_gas/api.py`，要点：

- **无签名、无 nonce、无 AES 加密**，纯表单 POST
- 请求头固定带 `oAuthType: AUTH_MOBILE`
- 凭据 = `Cookie: SESSION=xxx`，`acw_tc`（WAF Cookie）**实测非必需**
- 外层状态 `responseCode == "100000"`，内层看 `result.success`

| 接口 | 用途 |
|---|---|
| `POST /utility/userBind/getBindUserInfo` | 户号、表号、官方读数 + 抄表日、年度累计量 |
| `POST /fee/chart/iotBarChart` | 用量（`type=month` 日粒度 / `type=year` 月粒度），逐日数据可直接获取 |
| `POST /utility/personalCenter/getBillInfoListPage` | 账单历史（抄表间隔、各阶梯用量与单价，供离线核对） |

### 6.1 ⚠️ 滚动窗口陷阱

该接口的 `time` 参数**不代表自然月**，返回的是一个**往前滚动**的窗口
（实测今天 `2026-09-07`）：

```
请求 time=2026-09 → 返回 2026-08-01 ~ 2026-09-07（38 条，末条=当天 0，未结算）
```

窗口起点 ≈ 今天往前 37 天，只有终点随 `time` 月份变化。
所以**永远传当前月**，然后取 `readingTime` 最大、且日期早于今天的一条作为
「最近一日」，并过滤掉当天 0 值未结算行。

### 6.2 账期与阶梯核对（离线测试做了什么）

以本仓库 `2026-09-07` 快照为例，验证结论：

1. 官方读数 `207.0` @ `2026-08-30`；账单历史显示上一期 `07-30`（199.0）→
   `08-30`（207.0）间隔 **31 天**，账单量 `8.0 m³` = 读数差；
2. 账单 `8.0 m³` 全部为**第一阶梯 3.54 元/m³** → 应缴 `28.32` 元，
   与账单 `shouldFee` 完全一致；日数据窗口内（08-01~08-30）合计 `7.4 m³`，
   差额 `0.6` 恰为窗口外 `07-31`，账期核对吻合；
3. 2026 年账单累计 `145.0 m³` = 接口 `cycleCreditQty`（官方年度已结算累计），
   全年未触达第二阶梯；叠加本年度未结算增量 `3.7`（08-31 ~ 09-06）后，
   「当年累计用量」= `148.7 m³`，燃气费单价仍为第一阶梯 `3.54 元/m³`；
4. 月视图（自然月：8 月 8.1 / 9 月 3.0）与逐日数据按自然月求和**一致**，
   佐证逐日数据可直接用于核算。

### 6.3 需避开的接口

`/utility/en/rechargePayment/queryMeterInfo` 的请求与响应**均为 AES 密文**，
无法复现，**不要使用**。所需数据已由 `getBindUserInfo` 提供。

## 7. 抓包原始报文参考（脱敏）

> 来源：微信公众号「泰能天然气有限公司」→ 用能分析，Reqable 抓包整理。
> 正文已将 appid、token、SESSION、户号、地址、真实用量等一律替换为
> `<PLACEHOLDER>` 占位，仅保留**请求结构与响应字段说明**供二次开发对照。

### 7.1 鉴权链路要点

1. 访问渠道入口
   `GET channel-wechatserver.eslink.cc/co/channel/oauth?appid=<WX_APPID>&menu=<MENU_ID>&scope=snsapi_userinfo`
   → 302 跳转微信 OAuth 授权；
2. 微信回调带 `code` → 服务端换取 token（32 位 hex）；
3. 凭 token 调 `POST cloudselfhelp-mobile.eslink.cc/cloudselfhelp/login`
   （表单 `token=<TOKEN_32HEX>`，头带 `oAuthType: AUTH_MOBILE`）换取 SESSION；
4. **实测二次进入时 `token=` 传空仍返回成功** → 真正凭据是
   `Cookie: SESSION=<SESSION_UUID>`，`acw_tc`（WAF Cookie）非必需。

`login` 请求与成功响应（已脱敏）：

```http
POST http://cloudselfhelp-mobile.eslink.cc/cloudselfhelp/login
oAuthType: AUTH_MOBILE
X-Requested-With: XMLHttpRequest
Content-Type: application/x-www-form-urlencoded
Cookie: SESSION=<SESSION_UUID>; acw_tc=<WAF_TC>

token=<TOKEN_32HEX>
```

```json
{"message":"运行正确","responseCode":"100000","result":"success"}
```

### 7.2 户号 / 表号查询（getBindUserInfo）

```http
POST http://cloudselfhelp-mobile.eslink.cc/utility/userBind/getBindUserInfo
oAuthType: AUTH_MOBILE
Content-Type: application/x-www-form-urlencoded
Cookie: SESSION=<SESSION_UUID>

includeStoppedMeter=true&token=
```

成功响应 `result.userBindList[0]` 关键字段（值一律脱敏）：

| 字段 | 含义 |
|---|---|
| `userNo` / `userId` / `useridbak` | 用户号（8 位） |
| `meterNo` / `meterIds[].meterId` | 表号（12 位） |
| `meterReading` / `meterReadingDate` | 官方最新读数 / 抄表日 |
| `meterIds[].cycleCreditQty` | 官方年度已结算累计（户·年，1 月 1 日清零，供「当年累计用量」） |
| `meterIds[].ladder` | 平台当前阶梯（1 / 2 / 3） |
| `meterIds[].cycSurplus` | 各档剩余量（如 `83.00|120|99999999`，按已结算口径） |
| `meterIds[].cycEndDate` | 年度计费周期结束日（如 `2026-12-31`） |
| `meterIds[].price1` | 第一阶梯单价（元/m³） |
| `contactName` / `userTel` / `idNum` / `userAddress` | 用户身份信息（脱敏） |
| `companyDes` / `orgName` | 泰能天然气有限公司 |

### 7.3 用量图表（iotBarChart）

```http
POST http://cloudselfhelp-mobile.eslink.cc/fee/chart/iotBarChart
oAuthType: AUTH_MOBILE
Content-Type: application/x-www-form-urlencoded
Cookie: SESSION=<SESSION_UUID>

userNo=<USER_NO_8DIGIT>&meterNo=<METER_NO_12DIGIT>&type=year&meterType=17&startTime=&endTime=&time=2026
```

- `type=year` → 月粒度：`usageDetail[].useGasMonth` + `cycleTotalVolume`；
- `type=month` → 日粒度：`usageDetail[].readingTime`（`yyyy-MM-dd`）+ 逐日
  `cycleTotalVolume` / `cycleTotalValues`；
- 上述全部为纯表单 POST，**无签名、无 nonce、无 AES**；
- 日期窗口行为见 6.1「滚动窗口陷阱」。

### 7.4 需避开的接口

`POST /utility/en/rechargePayment/queryMeterInfo`：请求体与响应均为 AES 密文
（抓到的是整段不可读 base64），无法在 HA 中复现，**不要使用**（见 6.3）。
