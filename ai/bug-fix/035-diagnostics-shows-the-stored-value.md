# 035 · 诊断接口只回显「库里的值」，看不出进程还在用旧值

## 问题

[032](032-auto-retry-settings-need-a-restart.md) 的故障形态是：**库里已经改了，进程仍按旧值跑**。
它没有异常、没有日志，唯一的现场就是 `GET /api/diagnostics` —— 而那个接口当时回显的恰恰是
「库里的值」，等于告诉排查的人「配置是对的」，把人往反方向推。

同类形状在 [017](017-subtitle-status-never-reached-the-ui.md) 也出现过（后端有状态、界面没有）。
这是把一次教训固化成「下一次的第一现场」。

## 根因

诊断只读 `AppSettings` 与 `YtDlpService.get_dependency_status()`，**从来没有读过 `Setting` 表**，
也没有区分「库」与「运行态」这两个来源。于是：

- 别的进程（或手工）改了库 ⇒ 本进程不知道，诊断也看不见；
- 某处持有了构造时的快照（032 的调度器）⇒ 同样看不见。

## 修法

`DiagnosticsRead` 增加 `settings_effective`：每个配置项两栏 —— `stored`（库里）与
`runtime`（**这个进程实际在用的值**），外加 `differs`。

三条实现约束：

1. **运行值从真正持有它的对象上取**：下载相关读 `YtDlpService` 的字段（`apply_stored_settings`
   会推给它），自动重试三个旋钮读**调度器的策略**（`032` 之后它是每轮现取的）。
   只读 `AppSettings` 会漏掉「组件持有副本」这一类，而那正是 032 的形态。
2. **自检只读**：不调用 `apply_stored_settings`。灌回去的话，下一次「改了没生效」就再也查不出来 ——
   诊断会把现场抹平再报「一致」。
3. **`differs` 只报事实**：措辞不写「需重启生效」，因为差异的成因有两种 ——
   别的进程改了库（重新加载设置即可），或某处持有快照（必须改代码/重启）。诊断只能证明「两者不同」。

覆盖 12 项：下载目录、并发、默认清晰度、限速、重试次数、默认字幕语言、单视频并发下载数、代理、
完成提醒方式，以及自动重试的开关 / 间隔 / 上限。

界面：设置面板的「运行环境」区在有差异时多一条红字（列出每一项的两个值）；**一致时一个字都不说** ——
「一切正常」是噪音，还会让人以为它在盯着什么。

## 测试

| 用例 | 钉住什么 |
| --- | --- |
| `test_every_audited_key_reports_both_sides` | 每一项都有 `stored` 与 `runtime`；出厂状态不报差异（否则默认值全被标成「不一致」） |
| `test_a_stored_value_the_process_has_not_loaded_is_flagged` | 库改了、进程没跟上 ⇒ `differs=True`，且**只有这一项**被标出 |
| `test_the_audit_does_not_reload_the_settings` | 连调两次诊断，运行态不变（自检只读） |
| `test_the_auto_retry_knobs_read_the_scheduler_not_the_table` | 运行值来自调度器策略；走 `PUT /api/settings` 重新加载后两边一致 |
| `test_boolean_knobs_compare_in_their_stored_form` | 布尔在库里是 `"1"/"0"`，拿 `True` 比字符串会永远不一致 |
| `settingsDriftText`（前端 5 条） | 无差异返回 `null`、只列差异项、空值写「未设置」、未知键露键名、多项全列 |
| `diagnostics.test.tsx`（+2 条） | 有差异时那句话出现在页面上；一致时不出现 |

契约：`SettingEffective` 已登记进 `scripts/check_api_contract.py` 的 `SCHEMA_PAIRS`，
`docs/openapi.yaml` 补了字段定义 —— 少一处这道闸门就会红。

## 边界

- 只覆盖**有库副本**的配置项。纯环境变量（如 `YTDL_ARIA2C_ENABLED`）没有 `stored` 可言，不在对照里。
- 值一律按字符串比：`Path` / 列表 / 布尔都先折成登记时写库的形态。数字 `5` 与 `"5"` 视为相同。
- 诊断**不会**告诉你「怎么修」：它只指出哪一项不同、两边分别是什么。
