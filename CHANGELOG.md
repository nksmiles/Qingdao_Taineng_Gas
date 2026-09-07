# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)，所有值得记录的变更都会汇总在此文件。
版本格式：`v<主>.<次>.<修订>`，例如 `v1.2.1`。

## [1.2.1] - 2026-09-07

### 修复

- 修复「每日耗气量 YYYY-MM-DD」实体在 HA 日志中反复出现
  `state class 'measurement' which is impossible considering device class ('gas')` 警告的问题。
  HA 校验要求 `device_class=gas` 的传感器 `state_class` 只能为
  `None / total / total_increasing`，不允许 `measurement`。
  由于该系列实体是「单个已结算日的耗气量快照」，创建后值固定、不随时间变化，
  既非测量值也非累计量，因此移除了错误的 `state_class=measurement`（保持默认 None），
  仅保留 `device_class: gas` 用于展示，警告随之消除。

### 已知限制

- 「每日耗气量」实体不参与 HA 长期统计与能源面板（值固定且非累计口径），
  逐日核对 / 画图请直接使用其实体状态。
