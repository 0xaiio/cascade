# 分支提交差异对比（main vs 其它分支）

> 生成时间：2026-10-21 · 数据来源：仓库真实 `git` 命令（`rev-list --count`、
> `merge-base`、`branch -a`、`ls-remote`）。所有结论均可复现。

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
| `bilibili`（本地） | **0** | 27 | `cbc7732` | 已并入 main，落后 21 提交 |
| `origin/bilibili` | 0 | 27 | `cbc7732` | 同上（远程跟踪） |
| `fix/item-level-concurrency`（本地） | **0** | 100 | `0d4d921` | 已并入 main，落后 94 提交 |
| `origin/fix/item-level-concurrency` | 0 | 100 | `0d4d921` | 同上（远程跟踪） |
| `fix/progress-percent-stuck`（本地） | **0** | 101 | `f974ee5` | 已并入 main，落后 95 提交 |
| `origin/fix/progress-percent-stuck` | 0 | 101 | `f974ee5` | 同上（远程跟踪） |
| `origin/feature/audit-format-metadata` | **0** | 173 | `9eebbc1` | 已并入 main，落后 167 提交（该分支无本地跟踪） |

> 注：`fix/item-level-concurrency` 与 `fix/progress-percent-stuck` 的 merge-base 都在
> `0dd4b5d`（视频基准提交）之前，说明这两个 fix 分支在视频制作前就已合入；它们与本次
> 新增功能无关，只是尚未从远程清理。

## 三、本地 main 与远程的同步状态

| 对比 | 本地领先远程 | 远程领先本地 |
| --- | --- | --- |
| `main` vs 远端 `refs/heads/main`（`ls-remote`） | **0**（本轮推送后） | 0 |
| `main` vs 本地 `origin/main`（跟踪引用，**不可信**） | 13（本轮生成时） | 0 |

本轮（2026-10-21）推送前，远端 `refs/heads/main` 停在上一轮的文档提交 `90154df`，本地领先
**1 个修复提交**（`5ed7266`，字幕段数与文案）+ 本文所在提交；本轮已推送，现两端一致
（核对方式见下）。其余四个远程分支均**完全落后于** main（0 领先）。

> **核对方式有一条坑（连续两轮都撞上）**：以 `git ls-remote <url> refs/heads/main` 为准，
> **不要**用本地的 `origin/main` 跟踪引用 —— 它停在最后一次 fetch/push 的位置，而 `git push`
> 走显式 URL 时**不会**更新它。本轮生成时它落后 **13** 个提交，照它算「未推送」会得出完全相反的
> 结论。这个数每提交一次就 +1，写死它没有意义（上一轮文中记的「10」是复述了一个旧读数，
> 那一轮的真实值已经是 11）；要断言「是否已同步」只认 `ls-remote`。

## 四、自视频基准 `0dd4b5d` 以来的提交分布

- `main` 自 `0dd4b5d` 以来新增提交：**23 个**（截至 `5ed7266`，即 changelog 附录清单的口径）。
  本节的逐分支计数同口径；此后每在 `main` 上提交一次，这些「领先数」都会 +1，但**结论不变**（各分支独有提交均为 0）。
- 这 23 个提交均落在 `main` 上；其它分支因已并入 main，自然也包含这些提交（作为 main 历史的一部分）。

## 五、建议

1. **制作新视频用 `main` HEAD**，不要基于任何其它分支——其它分支都已过时且内容被 main 完全覆盖。
2. **清理过期分支**：`bilibili`、`fix/item-level-concurrency`、`fix/progress-percent-stuck`、
   `feature/audit-format-metadata` 均已并入 main，可删除（尤其是远程跟踪分支），避免混淆。
3. **推送本地 main**：已于 2026-10-21 完成（本地与 `origin/main` 一致）。若之后再产生新提交，
   制作前建议先 `git push` 保持远端同步（注意：本仓库 SSH 22 端口曾被本机代理拦截，
   必要时改用 `git push https://github.com/0xaiio/cascade.git main`）。
4. 如需在视频里说「最新版」，可在文案中标注版本锚点为当前 `main` 的 HEAD 提交 `614b06e`
   （生成时的 HEAD；其后的文档更新提交见 `git log`）。
