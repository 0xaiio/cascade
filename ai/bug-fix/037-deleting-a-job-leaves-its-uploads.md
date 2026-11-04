# 037 · 删任务不删投稿记录：摘要里一直有「已经不存在的任务」

## 问题

`JobManager.delete()` / `delete_items()` 只删 `JobEvent` / `JobItem` / `Job`。
`bili_upload` / `bili_season` 留在库里，而 `read_all_job_uploads()` 的 job 集合正是从这两张表
取并集 —— 于是 `GET /api/bilibili/uploads/summary` 会**一直为已不存在的任务回记录**。
`bili_auto_retry` 同样只增不减，那一条还会继续按点去问平台。

界面上看不出来（前端按 `/api/jobs` 渲染徽标），所以这是一处静默的存储面损伤
（[ai/bug-fix/README.md](README.md) 遗留 13，2026-10-中旬挂到现在）。

## 根因

删除只发生在「任务」这条线上，投稿是另一个域：`BiliManager` 的 worker 持有上传队列，
`JobManager` 连它的存在都不知道。于是「任务没了」与「它的投稿记录还在」可以同时成立，
而没有任何一层负责把两者对上。

## 修法

`BiliManager` 加两件事，路由在删除前后各调一次：

| 方法 | 作用 |
| --- | --- |
| `active_upload_message(job_id)` | 这个任务还有没有**在途**（`queued`/`uploading`/`submitted`）投稿；有就返回一句能照着做的话 |
| `forget_job(job_id)` | 删掉这个任务的三张表记录（`bili_auto_retry` / `bili_season` / `bili_upload`），返回行数 |

删除路径三处都接上（单条、批量、删光最后一个子视频）：

```
预检（有在途 ⇒ 409，什么都不删）  →  删任务行  →  forget_job()
```

四条决定：

1. **有在途就拒绝，而不是「取消再删」。** 投稿跑在另一个 worker 上，此刻它正拿着平台接口；
   取消需要把取消信号传进业务层（那会破坏「自动与手动复用同一套逻辑」），而删掉记录后
   它写回结论时会静默丢失。拒绝 + 一句「先等它结束或取消」是能立刻交付且不丢数据的选择。
2. **批量删除先整批预检**：不能删掉前几条、再因为某一条有在途投稿被拒 —— 那样用户既没删成，
   又丢了一半。
3. **`forget_job` 只按 `job_id` 删**，不碰别的任务（有用例钉住）。
4. **不改 `JobManager`**：它仍然只懂任务。跨域清理放在路由层（那里同时握着 `ctx.manager`
   与 `ctx.bili`），避免把投稿模型塞进下载域。

## 测试

| 用例 | 钉住什么 |
| --- | --- |
| `test_deleting_a_job_removes_every_upload_record` | 三张表都清空，且摘要里不再出现这个任务（前提：删之前它确实在摘要里） |
| `test_deleting_is_refused_while_an_upload_is_in_flight` | 409 + 任务与记录都留着 |
| `test_deleting_the_last_item_also_removes_the_records` | 走「删子视频」那条路同样会清 —— 它不走 `DELETE /api/jobs/{id}` |
| `test_a_busy_job_blocks_the_whole_batch` | 批量删除整批预检：一条忙 ⇒ 两条都不动 |
| `test_only_the_deleted_job_loses_its_records` | 别人的记录不受影响 |

A/B：把 `bili_manager.py` / `jobs.py` 还原到修复前，**5 条全红**。

## 边界

- **不做「按 job 取消在途上传」**：现在只有整体 `stop()`。要单条取消得把取消令牌传进
  `retry_upload` / `run_upload_work`，而那会让「自动与手动复用同一套逻辑」这条打折扣。
  在途时删除被拒，用户可以等它跑完（一般几十秒到几分钟）或先取消整个上传队列。
- 投稿记录删掉之后，B站 上的稿件**不会被删**（平台侧是另一回事），只是本机不再追踪它 ——
  这正是「删除任务」该有的语义，但值得说清楚。
