# 分支提交差异对比（main vs 其它分支）

> 生成时间：2026-10-22（刷新）· 数据来源：仓库真实 `git` 命令（`rev-list --count`、
> `merge-base`、`branch -a`、`ls-remote`）。所有结论均可复现。相对上一版（2026-10-21）：
> `main` 由 `614b06e` 推进至 `3dbcd83`（新增 16 提交），各分支「main 领先」计数随之增大，
> 且本地 `main` 当前**未推送**（领先 `origin/main` 23 个提交）。

## 一、核心结论

**`main` 是集成分支，已包含本项目所有分支的全部提交。** 对每一个其它分支，
`main..<branch>`（即在分支里、但不在 main 里的提交）数量均为 **0**——没有任何分支
持有 main 所没有的提交。因此：

- 制作新推广视频**应以 `main`（HEAD）为基准**，它是最新、最全的状态。
- 其余分支（`bilibili`、`fix/*`、`feature/audit-format-metadata`）都是**已被合并、
  已经过时的主题分支**，其内容已完整进入 main，可视为历史分支。

## 二、逐分支对比表

符号说明：
- `main..<branch>` = 分支有、main 没有的提交（**独有提交**）
- `<branch>..main` = main 有、分支没有的提交（**main 领先数**）

| 分支 | `main..<branch>`（独有） | `<branch>..main`（main 领先） | merge-base | 关系判定 |
| --- | --- | --- | --- | --- |
| `bilibili`（本地 = origin） | **0** | 37 | `cbc7732` | 已并入 main，落后 37 提交 |
| `fix/item-level-concurrency`（本地 = origin） | **0** | 110 | `0d4d921` | 已并入 main，落后 110 提交 |
| `fix/progress-percent-stuck`（本地 = origin） | **0** | 111 | `f974ee5` | 已并入 main，落后 111 提交 |
| `origin/feature/audit-format-metadata` | **0** | 183 | `9eebbc1` | 已并入 main，落后 183 提交（该分支无本地跟踪） |

> 注：`fix/item-level-concurrency` 与 `fix/progress-percent-stuck` 的 merge-base 都在
> `0dd4b5d`（视频基准提交）之前，说明这两个 fix 分支在视频制作前就已合入；它们与本次
> 新增功能无关，只是尚未从远程清理。

## 三、本地 main 与远程的同步状态

| 对比 | 本地领先远程 | 远程领先本地 |
| --- | --- | --- |
| `main` vs 远端 `refs/heads/main`（`ls-remote`） | **23**（2026-10-22 实测，未推送） | 0 |
| `main` vs 本地 `origin/main`（跟踪引用，**不可信**） | 数值不可信，忽略 | 0 |

自 2026-10-21 推送后，本地 `main` 又新增 **16 个提交**（`614b06e` → `3dbcd83`，含本文及
`5ed7266` 之后的 10 个提交），**均未推送**，`origin/main` 仍落后 `main` 共 23 个提交。
制作下一支视频前需先 `git push`（见第五节建议 3 的通道注意事项）。其余四个远程分支均
**完全落后于** main（0 领先）。

> **核对方式有一条坑（连续两轮都撞上）**：以 `git ls-remote <url> refs/heads/main` 为准，
> **不要**用本地的 `origin/main` 跟踪引用 —— 它停在最后一次 fetch/push 的位置，而 `git push`
> 走显式 URL 时**不会**更新它。本轮生成时它落后 **13** 个提交，照它算「未推送」会得出完全相反的
> 结论。这个数每提交一次就 +1，写死它没有意义（上一轮文中记的「10」是复述了一个旧读数，
> 那一轮的真实值已经是 11）；要断言「是否已同步」只认 `ls-remote`。

## 四、自视频基准 `0dd4b5d` 以来的提交分布

- `main` 自 `0dd4b5d` 以来新增提交：**33 个**（截至 `3dbcd83`）。其中 **23 个**作为功能基线
  冻结于 `5ed7266`（见 changelog 附录口径），其余 **10 个**为锚点之后的打磨 / 修复 / 文档同步
  （见 changelog 附录 B）。
- 各分支「main 领先数」随 `main` 推进而增大，但**结论不变**：所有分支 `main..<branch>` 均为 0，
  即 main 仍包含全部分支的提交；其它分支因已并入 main，自然也包含这些提交（作为 main 历史的一部分）。

## 五、建议

1. **制作新视频用 `main` HEAD**，不要基于任何其它分支——其它分支都已过时且内容被 main 完全覆盖。
2. **清理过期分支**：`bilibili`、`fix/item-level-concurrency`、`fix/progress-percent-stuck`、
   `feature/audit-format-metadata` 均已并入 main，可删除（尤其是远程跟踪分支），避免混淆。
3. **推送本地 main**：2026-10-21 曾推送一次，但此后又新增 16 个提交（含本文），**当前未推送**
   （本地领先 `origin/main` 23 个）。制作下一支视频前务必先 `git push` 保持远端同步
   （注意：本仓库 SSH 22 端口曾被本机代理拦截，必要时改用
   `git push https://github.com/0xaiio/cascade.git main`）。
4. 如需在视频里说「最新版」，可在文案中标注版本锚点为当前 `main` 的 HEAD 提交 `3dbcd83`
   （最近一次刷新时的 HEAD；其后的文档更新提交见 `git log`）。
