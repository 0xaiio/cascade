# 016 · 稿件审核期间，字幕被静默丢掉且再也补不上

- 状态：已修复
- 提交：本次
- 影响面：**每一个「稿件进入审核时已经有字幕」的投稿**。B站 的稿件先进审核/转码队列
  （`state=-30`）是常态，不是例外 —— 本机库里 11 条投稿有 3 条停在这个状态。
- 严重度：中高。不报错、不损坏数据，但**一条已经下载好的字幕永久丢失**，
  而界面当时给的回答是「已跳过」，事后给的是「这一条已经上传成功」——
  两句话都在暗示「一切正常」。

## 问题

投稿一条**带字幕**的视频，如果平台在 `_verify` 那一刻还查不到稿件详情
（审核/转码中，`x/web-interface/view` 返回 `-404`），`cid` 就是空的。
字幕是挂在分P（`cid`）上的，没有编号无处可挂，于是：

1. `bili_upload._attach_subtitle` 记下 `subtitle_message = "稿件还没核对到 aid/cid，跳过字幕上传"`，
   `subtitle_state` 停在默认的 `skipped`；
2. 用户看到「CC 字幕：已跳过」，而下载条目里那份 `.vtt` 明明在磁盘上；
3. 之后点「重试」：`status` 是 `reviewing`，属于「已经在平台上了」，
   `retry_upload` 只在 `row.subtitle_state == failed` 时才走「只补字幕」那条路 ——
   `skipped` 直接返回「这一条已经上传成功，没有重复投稿」；
4. 于是这条字幕**永远传不上去**，除非用户重投一条重复稿件（那更糟）。

### 实测依据（本机库，2026-10-21 复查）

`data/app.sqlite3` 的 `bili_upload` 共 11 条，`subtitle_state` 全是 `skipped`：

| created_at | status | aid | bvid | cid | verified_state | subtitle_message |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-18 06:34 | reviewing | 有 | 有 | **空** | `-30` | 没有已下载的字幕，跳过字幕上传 |
| 2026-10-19 09:22 | reviewing | 有 | 有 | **空** | `-30` | **稿件还没核对到 aid/cid，跳过字幕上传** |
| 2026-10-19 09:36 | reviewing | 有 | 有 | **空** | `-30` | **稿件还没核对到 aid/cid，跳过字幕上传** |

后两条对应的下载条目 `subtitle_language=en`、`subtitle_source=human`，`.vtt` 文件就在
`downloads/` 里 —— **有字幕、没传上去、结论是「跳过」**，正是本条。

## 原因

根因不是「cid 拿不到」（那由平台决定，改不了），而是**把两种完全不同的「没传」写成了同一个值**：

| 情形 | 该记什么 | 修复前记什么 |
| --- | --- | --- |
| 这个视频本来就没有字幕 | `skipped`（重投时会再判断一次，不需要用户做什么） | `skipped` ✓ |
| 有字幕，但当时挂不上去（拿不到 `cid`） | `failed`（要告诉用户、要能补） | `skipped` ✗ |

`skipped` 一词同时承担了「不需要」与「没做成」两种含义，而下游的
`retry_upload` 恰好**只对 `failed` 开放补传**。两处各自看都合理，接起来就是静默丢弃。

这属于本目录反复出现的那一类：**分支都写对了，判据写错了**（008 的 `return`、
010 的降级信号、011 的空令牌）。共同症状是「代码什么都没做，而界面在说已经做过了」。

## 修复方案

### 1. 有字幕却挂不上 ⇒ 记 `failed`（`bili_upload.py`）

第三条前置条件（`aid` / `cid` / `bvid` 齐不齐）从「设一句话」改成
「`subtitle_state = failed` + 一句能照着做的文案」：

> 稿件已提交（reviewing），但平台还没给出分P编号，字幕暂时传不了；稍后在字幕这一行点
> 「重传字幕」，只会补传字幕、不会重投视频

`skipped` 从此只表示「确实没有可传的字幕」（没有文件 / 文件已被删 / 格式不支持）。
顺带给三个分支各加一条日志 —— 修复前跳过**一条日志都没有**，排查时只能去翻库。

### 2. 补传的判据放宽到「有字幕文件且还没 `posted`」（`bili_manager.py`）

只改第 1 条的话，**已经躺库里的历史行救不回来**（它们的 `skipped` 是修复前写下的）。
所以 `published` / `reviewing` 的重试分支改成两把钥匙都能开门：

```
recoverable = subtitle_path is not None and row.subtitle_state != SUBTITLE_POSTED
```

- `failed`：这次确实失败了 → 允许补传；
- 有字幕文件但还没 `posted`（含历史 `skipped`）→ 同样允许补传。

**为什么这样不会变成「无条件重试」**：`_subtitle_of` 对没有字幕的条目返回 `None`
（`JobItem.subtitle_path` 为空、或文件已经不在了），此时 `recoverable` 为假，
仍然返回「这一条已经上传成功，没有重复投稿」。既有用例
`test_retry_on_a_published_upload_does_nothing` 钉的就是这一侧。

### 3. 重试的回复要如实（`bili_manager.py`）

原来的回复固定是「已重新上传字幕」——补传**可能再次失败**（稿件还在审核），
说「已上传」是把用户往错误方向引。改成按实际结论回话：

| 实际结论 | 回复 |
| --- | --- |
| `posted` | 已补传字幕 |
| `failed` | 字幕仍然没传上去：<原因> |

## 效果

| 项 | 修复前 | 修复后 |
| --- | --- | --- |
| 审核中投稿 + 有字幕 | `skipped`，无提示、无入口 | `failed` → 界面出现「CC 字幕」一行 + 警告 + 「重传字幕」按钮 |
| 用户点重试 | 「已经上传成功」，字幕永远丢 | 只补字幕、不重投；成功即 `posted`，失败则如实说「仍然没传上去」 |
| 历史 `skipped` 行（实测 2 条） | 救不回来 | 点一次「重传字幕」即可补 |
| 跳过原因 | 只有库里的 `subtitle_message`，无日志 | 三个分支各一条日志（`bili 字幕跳过` / `bili 字幕暂不可挂`） |
| 后端测试 | 1066 passed | **1069 passed**（+3） |
| 前端测试 | 200 passed | **208 passed**（+8，见 [017](017-subtitle-status-never-reached-the-ui.md)） |

## 验证

### 新增用例（3 条）

| 用例 | 断言什么 |
| --- | --- |
| `test_subtitle_that_cannot_get_a_cid_is_failed_so_it_can_be_retried`（`test_bili_upload.py`） | `state=-30` + `video=None` ⇒ 稿件 `reviewing`、`cid is None`、字幕 `failed`、文案提到「分P编号」与「重传字幕」、**一次字幕请求都没发** |
| `test_retry_on_a_reviewing_upload_only_reuploads_the_subtitle`（`test_bili_manager.py`） | 完整走一遍真实序列：审核中投稿 → `failed`；第一次重试仍拿不到 cid ⇒ `action=reverify` 且回复含「仍然没传上去」；把 `transport.video` 换成有 `cid` 的 ⇒ 补传成功 `posted`；**全程投稿命令只跑了 1 次** |
| `test_retry_can_still_attach_a_subtitle_that_was_skipped_before_the_fix` | 手工把行改回历史形态（`published` + `skipped`）⇒ 重试仍能补上，`oid=cid` |

### 变异测试（证明断言咬得住）

把第 1 条临时还原成修复前的写法（只设 `subtitle_message`、不动 `subtitle_state`），
只跑新增用例，两条都红在预期断言上：

```
E  AssertionError: assert 'skipped' == 'failed'
```

恢复后 `test_bili_upload.py` 的 10 条字幕用例全绿。

### 全量回归

- 后端 `1069 passed`（收集数 1069，36 个文件）
- `test_bili_manager.py` 84 条、`test_bili_upload.py` 77 条

## 未覆盖 / 如实说明      ← 必填

1. **没有再用真实账号跑一遍「审核中投稿 → 过审 → 补传」**。这条验证需要等平台走完
   审核（分钟到小时级），且会在真实账号上留一条稿件；本地用 `state=-30` + `video=None`
   复现的是**同一个判据**（`cid is None`），但「过审后 `view` 接口真的给 cid」这一点
   仍只有平台侧常识支撑。若过审后 `view` 仍取不到，补传会一直停在 `failed`，
   用户看到的是「字幕仍然没传上去：…还没给出分P编号」，不会静默。
2. **历史行需要用户手动点一次「重传字幕」**。没有做后台自动补传（那需要一个「稿件何时过审」
   的轮询源，而当前没有这个信号源）。已过审的老稿件点一下就补上，未过审的会如实说仍然失败。
3. **`subtitle_state` 的语义变紧了**：以前「有字幕但挂不上」记 `skipped`，现在记 `failed`。
   界面因此会多出一行警告 —— 这是有意的可见性变化，但**旧数据不会自动迁移**，
   库里已有的 `skipped` 仍按旧语义显示（不出现字幕行），只能靠重试那一刻被认出来。
4. **`recoverable` 会把「修复前"本来就没字幕"的历史行」也放行一次**：若这些条目后来
   手工补放了字幕文件，点重试会真的去补传。按设计这是对的（有文件就该传），
   但它确实让「重试」这一动作的语义比原来宽了一点。

## 风险与回滚

- **行为变化**：`reviewing` / `published` 状态下的重试，从「只在字幕 `failed` 时补传」变成
  「字幕 `failed` 或有字幕文件未 `posted` 时补传」。最坏情况是多传一次字幕
  （同一语言轨是覆盖，不是新增），**绝不会重投视频**（`reverify` 不跑投稿命令，
  用例已钉住「全程只有 1 次投稿命令」）。
- **文案变化**：`subtitle_message` 是自由文本，不进契约，前端直接显示，无需迁移。
- **回滚**：`git revert` 本次提交即可；数据面无需迁移（`skipped` / `failed` 都是旧有的取值）。

## 关联

- 需求：[FR-32 / FR-33](../../docs/requirements.md)（「视频投稿成功后把字幕一并提交」与
  「无字幕只跳过字幕」「提供只重传字幕的重试」）。
- 同一族的判据错误：[008](008-return-in-finally-swallows-the-real-error.md)、
  [010](010-unselectable-probe-raise-skips-the-fallback.md)、
  [011](011-biliup-refuses-a-token-less-cookie-file.md)。
  本条的判据错误形态是：**一个枚举值承担了两种业务含义，而下游按键值分支**。
- [017](017-subtitle-status-never-reached-the-ui.md)：同一次验收发现的另一半 —— 后端状态齐了，
  但「CC 字幕」那一行在 HEAD 里根本不存在（渲染代码与用例都漏在工作区）。
  只修本条不补 017，用户永远不知道有东西要补。
- 相关文档：`docs/technical.md`（B站 CC 字幕一节）、`docs/troubleshooting.md`（按症状查）。
