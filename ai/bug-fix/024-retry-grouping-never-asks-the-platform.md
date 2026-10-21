# 024 · 「重试归集」读的是本机旧记录，从不去平台核对分P编号 —— 视频早就过审了，合集永远停在「等待归集」

## 问题

`C++ Weekly - Ep 125 - The Optimal Way To Return From A Function` 投到 B站 之后，任务卡片上是这两行：

```
合集：等待归集   视频还在 B站 排队审核，暂时没法加进合集   等几分钟后点「重试归集」   [重试归集]
1. C++ Weekly - Ep 125 - … · 审核中      在 B 站查看
```

照提示点「重试归集」，**没有任何变化**：行里的文案、按钮、页顶回执（那句在视口外）都还是原样。
等多久都一样 —— 这条视频在 2026-10-21 就投上去了，之后一直这么显示。

## 原因

两个缺陷叠在一起：**一个让重试不可能成功，一个让它看起来像成功了。**

### 1. 根因：`cid` 的空洞没有任何路径去补

合集归集要的是 **`aid` + `cid`**（`UploadResult.episode_ready`，平台侧 `section/episodes/add` 的
`cid` 就是分P编号）。而 `cid` 来自 `x/web-interface/view`，这条接口对**审核/转码中的稿件**返回
`-404 啥都木有` —— 所以「投稿那一刻正在审核」的稿件，记录里 `cid` 一定是空的。

问题在于这个空洞**当初写下去，之后没有任何代码再去问平台一次**：

| 入口 | 它做什么 | 为什么不补 `cid` |
| --- | --- | --- |
| `retry_upload`（点「重试」） | 对 `published`/`reviewing` 直接回「已经上传成功，没有重复投稿」 | 它判的是**要不要重投**，不是「有没有新事实」 |
| `retry_season` → `_group_work` | 从**库里那几行**拼 `UploadResult`，再交给 `group_into_season` | 读的是库，不是平台；库里 `cid` 是空 ⇒ `ready` 为空 ⇒ 落回 `pending` |

于是集合那两个条件永远互相等待：**归集要 `cid`，`cid` 要平台过审，而平台过审后没人再来看一眼。**

实测（本机库 + 平台只读查询，2026-10-22 01:04）：

```
库：  bili_upload.status = reviewing   cid = NULL   verified_state = -30（“正在排队审核或转码”）
平台：GET x/web-interface/view?bvid=BV1CAnz6KEok
      code=0  state=0  cid=42927065206  aid=117478428514876     ← 已发布，编号早就有了
```

也就是说：界面说「排队审核」，平台说「已发布」，两个状态相差十几个小时。

复现命令与回包（旧代码，22 毫秒返回、一个请求都没发）：

```console
$ curl -X POST http://127.0.0.1:8001/api/bilibili/collections/37552bad…/group
HTTP 200 in 0.023s
{"message":"已重新尝试归集","season":{"status":"pending",
 "error":"视频还在 B站 排队审核，暂时没法加进合集"}}
```

`_group_work` 在 `ready` 为空时直接 `return SeasonOutcome(status=pending)`，**连 `create_season`
与 `add_episodes` 都没走到**，所以耗时是毫秒级、DNS 都没碰。

### 2. 「什么都没做」被报成了「已重新尝试归集」

`retry_season` 的措辞只有两种，判据是 `read.status != failed`：

```python
message="已重新尝试归集" if read.status != failed else "归集还是没有成功"
```

`pending` 掉进了第一个分支。于是回执说「已重新尝试」，行里那句「等几分钟后点重试」纹丝不动 ——
**两句话互相矛盾，而矛盾的方向正好让人不再点第二次**（用户的原话就是「点了没有任何反应」）。

## 修复方案

### 1. 重试时先去平台把「编号空洞」补上（`bili_upload.refresh_episode`）

```python
refresh_episode(context, *, title, bvid, index, item_id) -> UploadResult | None
```

- 认人**先按 `bvid`**（平台给的强身份），认不出来才退回原来的「按标题前缀」那套（老记录可能没有 bvid）。
- 认出来之后复用 `_apply_archive` 写 `aid/bvid/state/status/cover/cid` —— 这段映射原本只写在
  `_verify` 里，现在两个入口共用一份（两份拷贝一起漂移时，症状是「对账拿到 cid、回读拿不到」）。
- **读不到就返回 `None`**，调用方保持原记录不变。「查不到 ≠ 它不在平台上」：把一条 `reviewing`
  降级成「待核对」是本功能最贵的误判，用户会去重投一条已经投上去的稿件。

`bili_manager._refresh_episodes()` 只挑「`cid` 为空 **且** 状态属于 `ACCEPTED_STATUSES`」的行 ——
这个组合的含义就是「投上去了、平台当时还没给编号」。`failed`/`rejected` 不在其中：回读不会让它们变得能归集。

`_merge_refresh()` 写库的三条规矩：读回来是空的值不覆盖、状态只在**读到了 `state`** 时才改、
有变化才发事件（`bili_upload_refreshed`）。

### 2. 只有**显式重试**才回读

`_group_work(job_id, season_id, *, refresh=False)`；`retry_season` 传 `True`，自动归集
（`_maybe_group`，在最后一条稿件落地后立刻跑）保持 `False` —— 那个时刻 `cid` 缺失的含义是
「平台还没给」，立刻再问一次平台没有意义。

### 3. 回执按「这一次做了什么」分三档

`BiliSeasonRetryRead.action ∈ {grouped, failed, waiting}`：

| action | 含义 | 回执 |
| --- | --- | --- |
| `grouped` | 这次归集成了 | 「已重新尝试归集」 |
| `failed` | 发了请求又失败 | 「归集还是没有成功」 |
| `waiting` | **连请求都没发**（前提还不满足） | 「这次没有发起归集：<原因>」 |

同时把那句 advice 改成说清「等什么」与「那一下会做什么」：
`等 B站 审核通过后点「重试归集」—— 它会先去平台核对分P编号，再归集`。

## 效果

同一条记录、同一次点击（真实浏览器 + 真实后端 + 真实 B站 账号）：

```
POST …/group  →  6/10 帧采样到「归集中…」+ 禁用（真的在等网络了）
日志：bili 回读到新事实：upload=45ab20ec… ('reviewing', 117478428514876, 'BV1CAnz6KEok', None, '-30')
                      → ('published', 117478428514876, 'BV1CAnz6KEok', 42927065206, '0')
日志：bili 归集：合集 9355267 → 分区 10469380「正片」1 条
库：   season.status = grouped      upload: status=published  cid=42927065206
平台：GET view?bvid=BV1CAnz6KEok → ugc_season.id = 9355267  sections = [('正片', 9)]
```

界面同一时刻变成：

```
合集：已提交归集（B站 需人工审核）
1. C++ Weekly - Ep 125 - … · 已发布      在 B 站查看
```

**那个按钮从「一个永远不可能成功的动作」变成了「一次点击就把视频加进合集」。**

## 验证

| 用例 | 钉住什么 |
| --- | --- |
| `test_refresh_episode_fills_the_cid_the_upload_moment_could_not_get` | 回读补上 `cid`，且只读不投（`biliup` 一次都没跑） |
| `test_refresh_episode_prefers_the_bvid_over_the_title` | 标题被平台改写时仍认得出来（按 bvid）；按标题比对会认错人 |
| `test_refresh_episode_returns_none_instead_of_guessing` | 列表里没有 ⇒ `None`，不是「未核实」 |
| `test_refresh_episode_survives_a_broken_archives_read` | 列表读挂 ⇒ `None`，不把异常抛到归集那一步 |
| `test_retry_season_asks_the_platform_again_and_groups_once_it_can` | 完整真实链路：投稿时 `-30`/无 `cid` ⇒ `pending`；平台过审后**同一次重试**变成 `grouped`，且 `cid` 落库 |
| `test_retry_season_says_it_did_not_even_start_while_the_video_is_in_review` | 前提不满足 ⇒ `action=waiting`、回执「这次没有发起归集」、`episodes/add` **零次** |
| `test_retry_season_does_not_downgrade_a_row_when_the_platform_cannot_be_read` | 读不到平台时行保持 `reviewing/-30`（不降级成「待核对」） |
| `test_retry_season_returns_the_season_view`（API 层） | 回包带 `action` |

**承重测试**：把 `retry_season` 里的 `refresh=True` 改回 `False`（即修复前的行为），
`test_retry_season_asks_the_platform_again_and_groups_once_it_can` 立刻转红：

```
AssertionError: assert 'waiting' == 'grouped'
1 failed, 6 passed
```

回归：后端全量 `1138 passed`（141.73 s）。

## 风险与回滚

- **重试会多打两次只读请求**（稿件列表 + 稿件详情），只对「`cid` 为空且已投上去」的条数发；
  一条 stale 行 = 2 次 GET，不是 N 次。
- **归集请求现在真的会被发出去**：这正是要修的。代价是「点重试」不再是空动作 —— 一旦前提满足，
  它会往平台加视频（幂等：同一合集同一稿件重复加不会多一条，`add_episodes` 失败仍落 `failed`）。
- 回读只读 `GET`，不改平台任何东西；`_merge_refresh` 只补不删，不会抹掉本地已有的 `cid`。
- 回滚：`git revert <commit>`，无数据迁移（列结构没变）。已归集的历史行不受影响。

## 未覆盖 / 如实说明

- **「视频还在审核」这条真实分支没有在真实账号上跑通一遍**：本机库里另外 5 条 `reviewing`
  记录一查平台也全是 `state=0`，即都早已过审。`waiting` 这条路径的凭据是
  单测（`test_retry_season_says_it_did_not_even_start_…`）+ 浏览器探针里用合成响应量到的界面，
  不是「真实平台拒绝了一次」。
- **不会自动轮询**：审核通过后仍需用户点一次「重试归集」（advice 里写明了）。做后台轮询是另一件事，
  本轮没做，也不假装做了。
- **回读只翻稿件列表的第一页（20 条）**：投稿量大到一页装不下时，按 bvid 认人会落空
  （退回标题前缀比对，同样只看这一页）。这不是本轮引入的边界，但也没有修。
- `_refresh_episodes` 串行回读；合集条目在几十条以上时这次点击会明显变慢（未实测）。

## 关联

- [013](013-season-section-id-confusion.md)：同一处「重试」的另一条失败链 —— 那条是把合集 id
  当分区 id，重试也是「同一个错值再发一遍」。两条合起来的教训是：**重试必须先重新问一次平台**，
  否则它只是把上一次的输入再算一遍。
- [016](016-subtitle-lost-while-the-video-is-in-review.md)：同一个 `cid` 空洞的另一副面孔 ——
  那边它吃掉字幕，这边它吃掉归集。016 修的是「不要把未知记成跳过」，本条修的是「未知要能重新问」。
- [021](021-a-dropped-upload-link-looks-like-nothing-at-all.md)：同一条原则 —— 结论只能承诺
  「能证明走到哪一步」的东西。`waiting` 与 `failed` 分开，就是不让界面承诺它没做的事。
- 界面侧：[ai/ui/007](../ui/007-retry-grouping-looks-dead.md)（点下去没有观察点）、
  [ai/ui/008](../ui/008-the-upload-clock-counts-the-review.md)（审核期秒表还在数）。
