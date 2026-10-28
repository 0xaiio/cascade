# 028 - 时间戳用例与 worker 抢时序：同一份代码跑三次两种结果

| 项 | 值 |
| --- | --- |
| 发现时间 | 2026-10-28 18:34 +08:00（重构 [011](../refactor/011-flat-modules-into-feature-packages.md) 的基线全量跑） |
| 修复时间 | 2026-10-28 18:56 ~ 19:00 +08:00 |
| 起点 commit | `7542629`（发现）→ `1885faf`（重构提交后修复） |
| 结论 | **已修**。用例在 `POST` 之后补一次 `wait_until_finished`；改前整文件连跑 **1 红 1 绿**，改后 **3 绿**，全量 **2/2 绿（1223 passed）** |

---

## 1. 问题

`tests/test_api_bilibili.py::test_upload_timestamps_carry_a_timezone` **时红时绿**，同一份代码、
同一个命令，结果不一样：

```
# 全量第 1 次（重构前，工作区干净）
FAILED tests/test_api_bilibili.py::test_upload_timestamps_carry_a_timezone
1 failed, 1222 passed in 113.04s

# 单独跑这一条
1 passed in 2.04s

# 整个文件连跑两次
第 1 次：1 failed, 57 passed
第 2 次：58 passed
```

失败时的断言差异：

```
assert upload["finished_at"] == "2026-10-21T10:30:31.280768Z"
E  - 2026-10-21T10:30:31.280768Z
E  + 2026-10-28T10:34:16.593765Z      ← 「现在」
```

注意 `started_at` **没有**变 —— 只有 `finished_at` 被改写了，这正是定位原因的关键线索。

## 2. 原因

用例用的是**真的** `BiliManager`（`Client.__init__` 里 `bili=BiliManager(...)`，只把 `transport` /
`runner` 换成假的），所以 `POST /api/bilibili/uploads` 返回 200 时，条目只是**入队**，
worker 还在跑。用例紧接着手工写库：

```python
client.client.post("/api/bilibili/uploads", json=single_payload(client))   # ← 只是入队
with Session(client.engine) as session:
    row = ...
    row.status = "reviewing"
    row.started_at  = datetime(2026, 10, 21, 10, 29, 38, 727960)
    row.finished_at = datetime(2026, 10, 21, 10, 30, 31, 280768)
    session.commit()
```

谁先提交，取决于 worker 线程的调度：

- worker 先收尾 → 手工写入生效 → 断言读到钉住的时刻 → **绿**；
- 手工写入先落库 → worker 收尾时写 `finished_at = utc_now()` → 断言读到「现在」→ **红**。

`started_at` 之所以不受影响：worker 是在**开始上传**时写 `started_at`，收尾时只写 `finished_at`。
两次写之间夹进了用例的手工写入，于是只有后者被覆盖。

夹具里**本来就有**正确的等法 —— `Client.wait_until_finished()`，它自己的 docstring 已经写明
「不能拿 `queue.empty()` 当完成信号 …… 判据只能是数据库里的状态」。这条用例漏了调用它。

## 3. 修复方案

```python
created = client.client.post("/api/bilibili/uploads", json=single_payload(client)).json()
client.wait_until_finished(created["uploads"][0]["id"])   # ← 新增
```

只改这一处，理由：

- **不改夹具**：`wait_until_finished` 是既有机制，用法在同文件里已有先例（`assert client.wait_until_finished(upload_id) == "published"`）。
  在夹具里统一「POST 即等待」会改变另外 50 多条用例的语义（它们正是要测「上传中」这一刻的 409）。
- **不改被测代码**：服务端行为没问题 —— 收尾写 `finished_at` 正是它该做的；坏的只是用例的时序假设。
- **不放宽断言**：改成「只要带 `Z` 就行」会放过「值被覆盖」这一类错，而这正是本用例当初要钉的东西。

## 4. 效果

| 场景 | 改前 | 改后 |
| --- | --- | --- |
| 整文件连跑 ×3 | 1 红 1 绿（2 次实测） | **3/3 绿（58 passed）** |
| 全量 ×2 | 3 次全量里 2 次红 | **2/2 绿（1223 passed）** |

## 5. 验证

- 新增/修改用例：无新增（只补一次等待），用例的断言一字未动。
- 回归：`pytest -q --basetemp=tmp_pytest/run_fixfull_{1,2}` → `1223 passed` ×2。
- 反向取证：改前那份代码在同样的命令下红过（见 §1 的三次记录），不是「跑一次绿就算修好」。

## 6. 未覆盖 / 如实说明

1. **没有根治「真 worker + 假 transport」这类用例的时序假设**。这条路子整体是对的（它测的正是路由与编排的接线），
   代价是每条用例都可能踩同一个坑。本轮只补了这一条，**其它用例是靠运气绿还是靠等待绿，没有逐条审**。
   判据建议：凡是在 `POST` 之后手工改库的用例，都必须先 `wait_until_finished`。
2. **抖动是在别人的重构基线里发现的**，与目录重构无关：改动前后同一条用例、同样的红绿比例（§4 表）。
   这次是顺带修，不改变 [011](../refactor/011-flat-modules-into-feature-packages.md) 的结论。
3. **没在 CI 上复现过**。CI 是 GitHub runner（Linux），时序与线程调度和本机不同，可能本来就绿 —— 也就是说
   这个缺陷**过去可能一直靠运气被 CI 放过**。这一点无法从本机取证。

## 7. 风险与回滚

- 风险：`wait_until_finished` 有 10 s 超时，超时会抛 `AssertionError`。若某天假 transport 不再完成上传，
  这条用例会由「偶尔红」变成「稳定红」—— 这是**更好的**失败方式（红得明确，而不是随机）。
- 回滚：`git revert <commit>`。

## 8. 关联

- [020](020-the-progress-clock-is-eight-hours-off.md)：本用例服务的那个缺陷（时间串必须带 `Z`）
- [011](../refactor/011-flat-modules-into-feature-packages.md)：在它的基线全量跑里暴露出来
- [008](008-return-in-finally-swallows-the-real-error.md)：同一类「时序 + 静默」的问题，但那条坏在服务端
