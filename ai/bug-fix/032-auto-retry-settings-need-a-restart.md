# 032 · 关掉「自动重试」的开关，机器还在问平台：设置改完要重启才生效

## 问题

设置面板里三个旋钮（开关 / 间隔 / 次数上限）都写着「改完立刻生效」，
`api/routers/settings.py` 的注释更具体：

> B站 自动重试的三个旋钮。**改完立刻生效**：调度器每次问平台前都现取策略
> （`BiliManager.auto_retry_policy`），所以不用重启。

实际行为是**反的**：把开关关掉之后，到期目标照旧被处理，平台照旧被问一次。
用户以为自己关掉了，界面上「自动重试中…」还在一轮一轮出现。

## 根因

`BiliAutoRetryScheduler.__init__` 把策略存成属性：

```python
self.policy = policy          # 构造时的一份快照
```

而 `BiliManager` 传的是 `self.auto_retry_policy()` —— **调用的结果**，不是方法本身。
全仓没有任何地方更新 `scheduler.policy`，于是：

| 改的东西 | 期望 | 实际 |
| --- | --- | --- |
| 关掉开关 | 下一次 tick 什么都不做 | 照旧扫、照旧问平台 |
| 间隔 300 → 10 秒 | 10 秒后就开始新一轮 | 先按 300 秒睡完 |
| 上限 3 → 7 次 | 已经试过 3 次的目标继续等 | 判成 `exhausted`，把手动按钮交回用户 |

三个字段**本身**（schema 的 `ge=10, le=300` / `ge=1, le=10`）都有入口校验，
所以这不是「脏值」问题 —— 值是对的，只是没人去读。

## 取证

修复前跑的复现用例（现在留在 `test_bili_auto_retry_integration.py`）：

```
harness.settings.bili_auto_retry_enabled = False
assert await harness.manager.auto_retry.tick() == 0
→ assert 1 == 0          # 关掉了还在问平台
```

第二条更直接：

```
policy = harness.manager.auto_retry.policy
policy.enabled is False  → 实际 True
```

## 修法

1. **策略改成「每轮现取」**：`BiliAutoRetryScheduler` 接受
   `AutoRetryPolicy | Callable[[], AutoRetryPolicy]`，新增 `policy` 属性，
   传函数就每次调用。`BiliManager` 改传 `self.auto_retry_policy`（方法本身）。
   传对象仍然可用 —— 单测与一次性场景不必造闭包。
2. **睡眠分段**：整轮睡满 300 秒就听不到设置。`_sleep_one_round()` 按
   `POLL_SLICE_SECONDS = 1.0` 切片，每片重读间隔；间隔改小或关掉开关，**下一片**就生效。
   代价是每秒醒一次，可忽略。
3. **停机护栏**：睡到一半被 `stop()` 叫停时不再执行 `tick()` ——
   「立刻停」不该在关机途中再发一次平台请求。

三条改动都在 `bili_auto_retry.py`，`bili_manager.py` 只改一个实参。

## 回归防线

| 用例 | 钉住什么 |
| --- | --- |
| `test_a_policy_change_reaches_the_running_scheduler` | 三个旋钮改完，调度器读到的是新值 |
| `test_turning_the_switch_off_stops_a_running_scheduler` | 关掉开关后 `tick()` 返回 0，行仍停在 `waiting` |
| `test_the_scheduler_reads_the_policy_on_every_tick` | 上限 3 → 5 后，试过 2 次的目标继续等而不是 `exhausted` |
| `test_a_shortened_interval_takes_effect_mid_sleep` | 300 → 5 秒时这一觉只睡 5 秒，且每段 ≤ 1 秒 |
| `test_starting_twice_keeps_one_loop_and_stop_is_safe` | 重复 `start()` 只有一个循环；未启动时 `stop()` 不炸 |

A/B：把两个实现文件还原到修复前，以上 5 条里的 4 条变红
（`_sleep_one_round` 在旧实现里不存在，属新增 API）。
