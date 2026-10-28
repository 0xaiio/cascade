# 017 · 字幕结论从来没有在界面上出现过

- 状态：已修复
- 提交：本次（与 [016](016-subtitle-lost-while-the-video-is-in-review.md) 同一提交，两者互补：
  016 让字幕能被补传，本条给出「看得见、点得到」的入口）
- 影响面：**每一个用过 B站 投稿的用户**。后端三档状态（`posted` / `skipped` / `failed`）、
  三种文案、接口字段全都齐了，界面上却**一行都不显示**。
- 严重度：中高。不报错、不丢数据，但需求里「字幕传没传上去」这件事对用户完全不可见 ——
  等于把功能做在了看不见的地方。

## 问题

B站 投稿完成后，任务卡片里的稿件明细只有一行：「N. 标题 · 已上传」＋「在 B 站查看」。

- 字幕**传上去了**：没有任何提示（用户不知道它是 CC 字幕、不知道有多少条、不知道能关）；
- 字幕**没传上去**：也没有任何提示，更没有补传入口（用户只能看到稿件已上传，以为一切正常）；
- `GET /api/bilibili/uploads` 返回的 `subtitle_state` / `subtitle_lan` / `subtitle_message`
  三个字段，前端一个都没渲染。

## 原因

不是判据错，也不是逻辑错，而是**这批改动没有进版本库**：

| 文件 | HEAD 里的样子 | 工作区里的样子 |
| --- | --- | --- |
| `frontend/src/contracts/types.ts` | `subtitle_state: "posted" \| "skipped" \| "failed"` 内联联合类型，没有可复用的名字 | 提取成 `BiliSubtitleState` 并用于 `BiliUpload` |
| `frontend/src/bilibili/biliStatus.ts` | 无 `BAD_SUBTITLE_STATES` / `NOTABLE_SUBTITLE_STATES` / `isBadSubtitle` / `isNotableSubtitle` | 有，且 `isNotableUpload` 把「字幕失败」算进「有话要说」 |
| `frontend/src/bilibili/BiliJobStatus.tsx` | 只渲染稿件那一行 | 多渲染一行 `CC 字幕`：文案 + 失败时的警告图标与「重传字幕」按钮 |

`git show HEAD:frontend/src/bilibili/BiliJobStatus.tsx | grep -c subtitle` = **0**。
也就是说：上一轮把后端与文档都提交了，**前端呈现这一块漏在了工作区**，而且没有任何用例会红
（`biliStatus.test.ts` 也没覆盖字幕判据）。

这正是本仓库反复记录的那类风险：**没有测试的东西等于不存在**。漏提交之所以没被发现，
恰恰因为「字幕行渲染」没有任何断言 —— 后端 1066 条全绿，界面依旧空白。

## 修复方案

### 1. 补上渲染（沿用工作区里已成型的实现）

- 字幕行**只在有话说时出现**：`posted` / `failed` 渲染，`skipped` 不渲染
  （每条没字幕的稿件写一行会把卡片塞满，而投稿表单里早已写过「无字幕」）；
- 与稿件行**分两行**：一条 `published` 的稿件可以同时是字幕 `failed`，挤进同一行会让
  「已上传」和「字幕失败」看起来自相矛盾；
- 失败时给警告图标 + **「重传字幕」**按钮，复用 `onRetryUpload`（服务端只会补传字幕，
  不会重投视频，见 [016](016-subtitle-lost-while-the-video-is-in-review.md)）；
- `isNotableUpload` 把「字幕失败」也算作「有话要说」：否则一条**没有 `bvid`**（平台没回链接）
  的稿件会因为「没错误、没链接」而整行不渲染，字幕失败就在界面上彻底消失。

### 2. 补测试（这才是本条能结项的关键）

新增 [bili-subtitle-line.test.tsx](../frontend/src/bilibili/bili-subtitle-line.test.tsx) 5 条，
并在 [biliStatus.test.ts](../frontend/src/bilibili/biliStatus.test.ts) 补 2 条判据。断言的是
「用户看到什么、点了发生什么」，不是「函数返回 true」：

| 用例 | 断言什么 |
| --- | --- |
| 字幕传好了 | 出现「CC 字幕」与服务端那句文案；**没有**「重传字幕」按钮 |
| 字幕失败 | 出现「重传字幕」，点它调用 `onRetryUpload(这一条的 id)` |
| 本来就没有字幕 | 整个 `.bili-subtitle-line` 节点数 = 0 |
| 稿件本身没话说 | 没有 `video_url`、没有 `error`，仅字幕失败 ⇒ 仍然渲染出**一行**字幕行 |
| 已上传 + 字幕失败 | 恰好两行，第二行带 `is-bad`；第一行文案仍是「已上传」 |
| `isBadSubtitle` / `isNotableSubtitle` | 只有 `failed` 算出问题；`posted` + `failed` 算有话要说 |

## 效果

| 项 | 修复前 | 修复后 |
| --- | --- | --- |
| 字幕上传成功 | 界面上什么也没有 | 「CC 字幕：CC 字幕已上传（N 条，en），观众可在播放器里开关」 |
| 字幕失败 / 审核中挂不上 | 什么也没有，无处可点 | 「CC 字幕：<原因>」+ 警告 + **「重传字幕」** |
| 本来就没字幕 | 无（正确） | 无（保持） |
| HEAD 是否包含这块 | 否 | 是 |
| 前端测试 | 17 文件 / 200 passed | **18 文件 / 208 passed**（+1 文件 +8 例） |

## 验证

- `npx vitest run --environment jsdom` → 18 文件 / **208 passed**，无 error
- `npx tsc --noEmit` → 退出码 0
  （**这条以前是红的**：`biliStatus.test.ts` 里的 `isNotableUpload` 调用没传 `subtitle_state`，
  属于同一批未落地的改动；本轮一并补齐）
- 后端 `1069 passed`（与 016 一起跑）

## 未覆盖 / 如实说明      ← 必填

1. **没有在真实浏览器里看过这一行的观感**。`.bili-subtitle-line` / `.is-bad` 是既有样式类
   （错误行已在用），本轮没有新增 CSS，但字体/间距在真实页面上的效果没有截图取证。
2. **「重传字幕」按钮在真实链路里点下去会怎样，只有服务端单测覆盖**。前端只断言它打到了
   `onRetryUpload`；「服务端真的补传成功」由 016 的 `test_bili_manager` 覆盖，
   两者之间没有端到端测试（需要真实账号）。
3. **没有做视觉回归**：字幕行出现后任务卡片会高一行，极端情况（多条失败）下可能挤。
   这是「多一行信息」的必然代价，未做密度调优。

## 风险与回滚

- 只加渲染与测试，**无契约变化**（字段早就在 `BiliUploadRead` 里）。
- 唯一的行为变化是任务卡片多出一行；`skipped` 不渲染，所以绝大多数历史投稿的观感不变。
- 回滚：`git revert` 本次提交；无需数据迁移。

## 关联

- [016](016-subtitle-lost-while-the-video-is-in-review.md)：同一次验收发现，二者互补 ——
  016 让「有字幕没传上去」变成可补的状态，本条让用户**看得见、点得到**。
  只修 016 不补本条，用户永远不知道有东西要补。
- 判据与 [014](014-a-dead-notify-switch.md) 同源：**界面承诺了、实现没跟上**（这里是反过来 ——
  实现了，界面没跟上）。验证办法也一样：找「它被执行过」的正面证据（这里有 DOM 节点可数）。
