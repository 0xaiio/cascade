# 033 · 手动补传字幕成功了，界面还在倒计时：响应里那份读模型比库里旧一步

## 问题

用户点「重传字幕」，字幕**真的补上了**（`subtitle_state = posted`，
列表里也会显示「已补传字幕」）。但同一次响应的 `upload.auto_retry` 仍然是：

```json
{ "state": "waiting", "next_attempt_at": "2026-11-03T14:20:05Z", "attempts": 2 }
```

前端拿这份响应直接渲染，于是「已补传字幕」旁边那颗 `下一次自动重试 00:35`
继续走 —— 看起来像机器还在等一件已经做完的事。归集那条路一样：
手动「重试归集」成功后，`season.auto_retry.state` 仍是 `waiting`。

## 根因

时序。两条路的「收工」都发生在**读模型构造之后**：

```
retry_upload
  └─ _reverify_work()
       ├─ _store_result()                     落库
       ├─ publish(bili_upload_finished)       ← 界面据此刷新，此时库里还是 waiting
       └─ read_upload(..., auto_retry=...)    ← 读模型在这里定型（waiting）
  └─ if posted: _close_auto_retry()            ← 收工（done）在这里才发生
```

`retry_season` → `_group_work()` 同样是「先构造 read、后 close」，位置在
`bili_manager.py` 的 `retry_season` 里。

还有第二层：`_close_auto_retry()` **不发事件**（只有 `_register_auto_retry` 发），
而 `bili_upload_finished` / `bili_season_finished` 是在收工之前发的。
于是被那条事件叫去刷新的客户端，读到的仍是 `waiting`；要等下一次**无关**刷新
（别的事件、切标签页）才纠正过来。

## 取证

修复前（现在留在 `test_bili_auto_retry_integration.py`）：

```
result = await harness.manager.retry_upload(upload_id)
result.upload.subtitle_state → "posted"
result.upload.auto_retry.state → "waiting"     # 库里 meanwhile 是 done
```

归集侧同形：`result.season.auto_retry.state → "waiting"` 而 `season.status == "grouped"`。

## 修法

1. **收工之后重读一次**：`retry_upload` 与 `retry_season` 在 `_close_auto_retry(...)`
   之后重新构造返回的读模型（同一个 session、同一份 `read_auto_retry`），
   响应与库一致。
2. **收工也发事件**：`_close_auto_retry` 提交后 `publish({"type": "bili_auto_retry_changed", ...})`。
   这样即使客户端先被 `*_finished` 事件叫去刷新（读到 `waiting`），
   紧随其后的这条会让它再刷一次，拿到 `done`。

两条都不是「多此一举的刷新」：`done` 会让倒计时整块消失，这正是用户想看到的确认。

## 回归防线

| 用例 | 钉住什么 |
| --- | --- |
| `test_a_successful_manual_subtitle_retry_returns_the_finished_state` | 补传成功 ⇒ 响应里 `auto_retry.state == done` |
| `test_a_successful_manual_grouping_returns_the_finished_state` | 手动归集成功 ⇒ 响应里 `auto_retry.state == done` |

两条在修复前都是 `waiting`，修复后转绿（A/B 已验证）。
