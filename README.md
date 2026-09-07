# 青岛泰能燃气 · Home Assistant 集成

从微信公众号「泰能天然气有限公司」背后的 **ESLink 易联云** 平台读取燃气数据，
在 Home Assistant 中展示每日用气量、本月累计与燃气表读数。

> 参考项目：[sunfang1cn/hass-hangzhou-ranqi](https://github.com/sunfang1cn/hass-hangzhou-ranqi)（杭州燃气）
> 本集成沿用其架构，接口与鉴权方式针对青岛泰能（ESLink）重新适配。

---

## ⚠️ 使用前必读

1. **必须抓包获取 SESSION**
   微信 OAuth 的 `code` 只能由微信客户端产生，服务端无法自动登录。
   因此本集成**不做自动登录**，需你手动抓一次包拿到 `SESSION` Cookie 填入。

2. **只能在家庭宽带 / HA 局域网内运行**
   该平台网关按出口 IP 拦截。实测云服务器、公司网络、代理环境一律返回
   `403 policy_default_denied`，**与代码无关**。

3. **SESSION 会过期（周期未实测）**
   过期后 HA 会弹出「重新认证」，在集成选项里更新即可，**无需删除重建**。

---

## 功能

| 传感器 | 说明 | 类型 |
|---|---|---|
| `累计表读数` | 官方读数 + 抄表日之后的已结算用量之和 | `total_increasing`（可进能源面板） |
| `最近一日用量` | 最后一个**已结算日**的用气量 | `total` |
| `本月累计用量` | 本月已结算日用气量合计 | `total` |

- 默认 **6 小时**更新一次（燃气数据每日才变，分钟级轮询会触发风控）
- 全 `async` 实现，不阻塞 HA 事件循环
- 更新失败时**保留上次的值**，不会让图表出现断线
- Options Flow：不改配置就能更新 SESSION、切换校准开关

---

## 安装

### 方式一：手动安装

```bash
# 把整个目录复制到 HA 配置目录
cp -r custom_components/qingdao_taineng_gas /config/custom_components/
```

重启 Home Assistant → **设置 → 设备与服务 → 添加集成** → 搜索「泰能燃气」。

### 方式二：HACS 自定义仓库

把本仓库放入 GitHub，HACS → 右上角菜单 → 自定义存储库 → 添加。

---

## 获取 SESSION（Reqable 抓包教程）

> 微信 OAuth 的 `code` 只能由微信客户端产生，服务端无法自动登录，
> 因此本集成**不做自动登录**，需要你手动抓一次包。下面以 **Reqable** 为例
> （Charles / Fiddler 操作同理）。**电脑微信**的内置浏览器会走系统代理，比手机更好抓。

### 第 1 步：准备

1. 电脑安装 [Reqable](https://reqable.com/)（Windows / macOS）
2. 电脑登录**微信 PC 版**
3. 确认当前在**家庭宽带**（网关会拦截云服务器 / 公司网络 / 代理的出口）

### 第 2 步：开启抓包环境

1. 启动 Reqable，点击工具栏上的**抓包开关**（红色圆钮）开始抓包
2. 首次使用会提示**安装根证书**，请务必完成：
   - Windows：按提示一键安装到「受信任的根证书颁发机构」
   - macOS：证书会装入钥匙串，需在「钥匙串访问」里把它设为**始终信任**，
     再重启 Reqable
3. 确认**系统代理**已开启（Reqable 开启抓包后会自动设置系统代理；
   界面上能看到本机代理地址，形如 `127.0.0.1:9000`）
4. 先**清空**当前会话列表（避免噪音干扰）

> 若只看到 `CONNECT` 隧道而看不到具体请求内容，说明 HTTPS 解密没生效，
> 回到第 2 步重新安装并信任根证书。

### 第 3 步：在微信里打开燃气页面

1. 把下面的链接发给微信「文件传输助手」并点开
   （也可发到任意聊天窗口，**不要**直接在浏览器打开，那样拿不到 SESSION）：

   ```
   https://cloudselfhelp-mobile.eslink.cc/?showyqje=0#/energyAnalysis
   ```

2. 等待页面加载完成并正常显示用气量曲线（多等几秒，页面会自动请求数据）
3. 此时 Reqable 列表应不断出现 `cloudselfhelp-mobile.eslink.cc` 的请求

> 若一直抓不到，改用文末的「手机抓包」方式。

### 第 4 步：找到 SESSION

1. 在 Reqable 顶部的**搜索 / 过滤框**输入 `cloudselfhelp-mobile.eslink.cc`，
   只保留该主机的请求（可同时隐藏 js/css/图片等静态资源）
2. 点开任意一条 POST 请求，推荐这两条之一：
   - `/utility/userBind/getBindUserInfo`（绑定的户号、表号、官方读数）
   - `/fee/chart/iotBarChart`（日/月用量）
3. 在右侧详情面板切到**请求 → Headers（请求头）**，在 `Cookie` 字段里找到：

   ```
   SESSION=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```

   复制 `SESSION=` 后面那一串（UUID 格式）

   > 💡 找不到时，可在该面板内按 `Ctrl+F` 搜索 `SESSION=`。

4. 粘贴到 HA 的配置表单；SESSION 过期时到**集成选项**里原地更新，无需删除重建

### 第 5 步（可选）：导出 HAR 供离线测试

`tools/test_cumulative_reading.py` 会自动读取仓库根目录的 HAR 快照做算法验证，
重新抓包后可顺手导出一份更新：

1. 确认已抓到上面第 4 步的两条请求（`getBindUserInfo` 与 `type=month` 的 `iotBarChart`）
2. 在 Reqable 中选中这两条请求 → 右键 → **导出 → HAR**
3. 保存到本仓库**根目录**，文件名以 `channel-` 开头、以 `.har.json` 结尾即可被自动识别，
   例如 `channel-wechatserver.eslink.cc_2026_09_06.har.json`
   > ⚠️ HAR 内含真实户号 / 表号 / 地址等隐私，已被 `.gitignore` 排除，**切勿上传 GitHub**。
4. 运行 `python3 tools/test_cumulative_reading.py` 校验

### 手机抓包（电脑微信抓不到时）

1. 手机与电脑连接**同一 WiFi**
2. Reqable 首页 / 设置里查看本机代理地址，形如 `192.168.x.x:9000`
3. 手机 WiFi 设置 → 代理 → 手动，填上述 `IP:端口`
4. 手机浏览器访问 Reqable 提示的证书地址，下载并**安装、信任根证书**
5. 手机微信打开第 3 步的链接，回到 Reqable 按第 4 步查找 SESSION
6. 用完记得把手机代理关掉

### 验证

填之前可以先跑一下 `tools/verify_session.py`（见「附带工具」）确认 SESSION 有效。

---

## 配置项

| 字段 | 必填 | 说明 |
|---|---|---|
| `SESSION` | ✅ | 抓包得到的 Cookie 值 |
| 户号 | ❌ | 留空则自动取默认户号 |
| 表号 | ❌ | 留空则自动取默认表号 |
| 表类型 | ❌ | 默认 `17`（民用 NB-IoT 表） |

配置时集成会**真实调用接口校验**，SESSION 无效会直接给出中文提示。

---

## 传感器属性

**累计表读数** 额外提供：
- `base_reading`：官方抄表读数
- `meter_reading_date`：官方抄表日期
- `settled_date`：最后已结算日期
- `data_lag_days`：数据滞后天数（正常为 1）

**最近一日用量** 额外提供：
- `reading_date`：该用量对应的日期
- `recent_days`：最近 7 天用量明细（可用于 ApexCharts 卡片画图）

---

## 常见问题

### Q：累计读数比我公众号里看到的多/少一点？

官方接口返回类似：

```json
meterReading: "207.0"        // 官方读数
meterReadingDate: "20260830" // 抄表日期
```

但**无法判断**这 207.0 是否已包含 8-30 当天的 0.4 m³ 用量。

- 默认按**不含**处理（从抄表日次日起累加）
- 若 HA 显示的读数比公众号**恰好少一天用量**，在集成选项里打开
  **「官方抄表读数已含抄表当天用量」** 开关即可校准

### Q：为什么「最近一日用量」显示的是昨天的？

燃气数据滞后一天。当天那条数据通常是 `0`（未结算），
所以集成取的是**最后一个已结算日**（即昨天），避免出现 0 值干扰。

### Q：提示 403 / policy_default_denied？

网络出口被网关拦截。请确认：
- 关闭科学上网 / 代理
- 不要在公司网络
- 直接在 HA 所在机器上运行

### Q：SESSION 有效期多久？

未实测。建议装好后隔 3 天、7 天各跑一次 `tools/verify_session.py`，
记录下哪次开始失效，就能估算出周期。

### Q：公众号里的身份证号、手机号会不会泄露？

接口 `/utility/userBind/getBindUserInfo` 确实会返回这些字段，
但**本集成只读取户号、表号、读数，不存储也不记录任何敏感字段**，
日志中的户号/表号均做脱敏处理（只显示末 4 位）。

---

## 接口说明（供二次开发）

详见 `custom_components/qingdao_taineng_gas/api.py`，要点：

- **无签名、无 nonce、无 AES 加密**，纯表单 POST
- 请求头固定带 `oAuthType: AUTH_MOBILE`
- 凭据 = `Cookie: SESSION=xxx`，`acw_tc`（WAF Cookie）**实测非必需**
- 外层状态 `responseCode == "100000"`，内层看 `result.success`

| 接口 | 用途 |
|---|---|
| `POST /utility/userBind/getBindUserInfo` | 户号、表号、官方读数 |
| `POST /fee/chart/iotBarChart` | 用量（`type=month` 日粒度 / `type=year` 月粒度） |

### ⚠️ 滚动窗口陷阱

该接口的 `time` 参数**不代表自然月**：

```
请求 time=2026-09 → 返回 2026-08-01 ~ 2026-09-06（37 条）
请求 time=2026-08 → 返回 2026-08-01 ~ 2026-09-01（32 条）
```

两次**起点相同**（今天往前推 36 天），只有终点不同。
所以**永远传当前月，然后取 `readingTime` 最大的一条**。

### 需避开的接口

`/utility/en/rechargePayment/queryMeterInfo` 的请求与响应**均为 AES 密文**，
无法复现，**不要使用**。所需数据已由 `getBindUserInfo` 提供。

---

## 附带工具（仅本地使用，随 .gitignore 排除、不上传 GitHub）

> SESSION / 户号 / 表号 / 表类型等凭据均**在运行时由用户输入**，脚本内无任何预填值。

| 文件 | 用途 | 联网 |
|---|---|---|
| `tools/verify_session.py` | 交互式验证 SESSION 有效性、是否被网关拦截，并自动读出默认户号/表号/表读数 | 需要 |
| `tools/test_cumulative_reading.py` | 离线算法验证（读取 HAR 快照），不依赖 HA 环境 | 不需要 |
| `tools/README.md` | 工具详细使用说明与验收对照表 | — |

运行验证脚本（需家庭宽带/HA 局域网）：

```bash
pip3 install requests
python3 tools/verify_session.py
```

运行离线算法验证（无需联网）：

```bash
python3 tools/test_cumulative_reading.py
```

---

## 免责声明

本项目仅供个人学习与技术交流，用于读取**自己名下**的燃气数据。
请遵守微信与泰能天然气的用户协议，勿用于高频爬取或其他商业用途。
因使用本项目造成的任何问题，作者不承担责任。
