# 019 · 投稿表单里的标题长度校验对合集从未生效

## 问题

用户问的是「投稿时能不能就看出标题超限」。把现状摊开看，答案是**一半能、一半完全不能**：

1. **单视频**：问题列表里确实会写「标题超出 N 个字」，但**标题框旁边那个 `71/80` 只给数字** ——
   超限与合规长得一模一样（同一档灰字），用户得先读数字、再去别处比对上限才知道自己超了。
2. **合集**：预检量的是**模板本身**。而模板是 `{job} · 第 {index}/{count} 集 · {title}`
   这种 36 个字的东西，离 80 还差一半 —— **它永远合规**。真正会被平台拒的是**渲染后**的每一条
   （同一个模板 + 一条 71 个字的原标题 → 88 个字）。

也就是说：合集的标题长度校验**从来没有拦住过任何一次超限**，界面上却一直显示着一个「没问题」的
字数。用户点提交之后才吃到 400；更糟的情况是提交通过了、传到一半才失败
（见 [018](018-a-long-file-name-looks-like-a-login-failure.md)，平台这条 80 字符的账算得很细）。

## 原因

### 1. 合集预检量错了对象

`frontend/src/bilibili/biliForm.ts` 的 `localPreflight`（HEAD，`147` 行）：

```ts
...(metadata.title.length > limits.title ? [`标题超出 ${...} 个字`] : []),
```

`isCollection` 时 `metadata.title` 是**模板**，不是要提交的那串字。而服务端提交时是**逐条渲染后**
校验的（`backend/app/bilibili/bili_upload.py:659` `validate_collection` → `render_collection` → `validate`）。
两处量的不是同一个东西 ⇒ 前端这个「通过」是假的。

判据：**服务端量什么，前端就得量什么。** `{title}` 只有 7 个字符，量模板等于什么都没查。

### 2. 计数只有数字、没有结论

`BiliUploadDialog.tsx`（HEAD）里 4 处 `.char-count` 全是 `className="char-count"` + 一个
`{len}/{limit}` 字符串，`is-over` 这条分支根本不存在 ——
「超没超」要用户自己做一次减法才能知道：

```
$ git show HEAD:frontend/src/bilibili/BiliUploadDialog.tsx | grep -n 'className="char-count"'
257:            <span className="char-count">
424:            <span className="char-count">
439:            <span className="char-count">
477:            <span className="char-count">
```

## 修复方案

1. **抽一个 `charBudget(value, limit)`**：把 `length / limit / over / label / warning` 合成一个
   结构体返回。**「超没超」必须和数字一起算出来** —— 分开渲染就会出现「同一个事实写两遍、迟早
   对不上」。`over` 恒不为负（否则「-3」会被读成「还剩 3」）。
2. **合集量渲染后的每一条**：新增 `collectionTitleBudget()`，用 `prefill.items` +
   `prefill.job_title` 重放一遍渲染（与逐条预览共用 `renderItemTitle`，避免「预览 79 字、
   判据 85 字」），返回 `template`（界面要显示用户正在打多少字）与 `rendered` / `longest`
   （**只有后者参与判据**）。
3. **模板那一档不参与判据**：服务端不校验模板本身。构造过反例 —— `{index}` 是 7 个字、渲染出来
   是 `"01"` 2 个字，模板长度可以远大于渲染结果；拿模板去拦提交就会出现「界面说超了、
   服务端说没事」。
4. **界面**：标题框旁在超限时追加「超出 N 个字」并加 `is-over` 与 `aria-invalid`；合集再补一行
   `渲染后最长 N/80（第 X 条，超出 M 个字）`。多条超限时先说有几条、再点名**超出最多的那条**
   （用户改模板时最需要的是「还差几个字」）。
5. **上限仍来自服务端**（`prefill.limits.title`），前端不写死 80 —— 见
   [012](012-preflight-contradicts-itself-and-the-server.md)。服务端 `validate` 仍是唯一权威。

## 效果

| 场景 | 修复前 | 修复后 |
| --- | --- | --- |
| 单视频标题 90 字 | 框旁 `90/80`（灰字），结论只在下方列表里 | 框旁 `90/80 · 超出 10 个字`（`is-over` + `aria-invalid`），输入**不截断** |
| 合集模板 `P {title}`（8 字）、第 2 条渲染后 82 字 | 一个「没问题」的 `8/80`，提交按钮可点 | `渲染后最长 82/80（第 2 条，超出 2 个字）` + 列表点名第 2 条，按钮禁用 |
| 合集模板 84 字、渲染后 24 字（`{index}`  repeated） | 会被模板长度挡住（误报） | 放行（与服务端一致） |

## 验证

| 用例 | 钉住什么 |
| --- | --- |
| `biliForm.test.ts` · 模板合规、渲染后超限 —— 必须报出来，并点名是哪一条 | 判据量的是渲染结果（`第 2 条` + `超出 2 个字`） |
| 只量模板就会给出假的通过 | 反证：同一模板在 `items: []` 下是「没问题」的，有 items 就有 1 条问题 |
| 模板自己超长、渲染后合规 —— 不拦 | 模板那一档不参与判据 |
| 多条超限：先说有几条，再点名超出最多的那条 | 报的是 `over` 最大的那条，不是先遇到的那条 |
| 单视频量的仍然是标题本身 —— 传了 items 也不参与 | 合集改动不能把单视频那条改坏 |
| 条目还没读到时不报假警 | 空 `items` 不是「全都合规」的相反面 |
| `longest` 是渲染后最长的那条；等长时取先出现的 | 界面上「第 X 条」的指向稳定 |
| 判据与预览用的是同一个渲染结果 | 消除「预览 79 字、判据 85 字」 |
| `charBudget`：超没超与数字一起算出来 / `over` 永不为负 | 计数那一行本身的语义 |
| `bili-upload.test.tsx` · checks the length of every rendered title, not the length of the template | 弹窗里改模板后**即时**出现「渲染后最长…」与禁用按钮；改回短模板放行 |
| a template that fits everywhere leaves the rendered line unmarked | 合规时**不带** `is-over`、不含「超出」（否定式断言要有 0 值支撑） |
| says how many characters over right next to the title box | 单视频框旁的 `90/80 · 超出 10 个字` + `aria-invalid` |
| `styles.test.ts` · gives the over-limit character count its own tone | `.char-count.is-over` 必须存在、且只改颜色与字重（版式交给 `.field` 的 grid） |

**真实浏览器**（`tmp_acceptance/ui_bili_title_len.py`，**41 条断言全过**）：合成一个 2 条目 playlist
（第 2 条原标题明显更长）+ 合成合集预填，其余请求走真实后端。量到的关键值：

```
渲染后最长 159/80（第 2 条，超出 79 个字）   color=rgb(180, 35, 24)  weight=700  可见（宽高 > 0）
问题列表：有 2 条渲染后的标题超限，最严重的是第 2 条「…」（超出 79 个字）
提交按钮：disabled → 改成 {title} 后 enabled，那一行回到 rgb(51, 65, 85) 且文案里没有「超出」
单视频：90/80 · 超出 10 个字 + rgb(180, 35, 24) + aria-invalid=true + 输入框描边染红，输入未被截断
```

**它同时证否了一件事**：`.char-count.is-over` 若只在 TSX 里加类名、而 `styles.css` 漏了这一档，
组件用例照样全绿（jsdom 不加载样式表），用户看到的却是灰字 —— 所以 CSS 里那一档
单独有一条源文不变式钉着。

**承重测试**：把 `titleProblems()` 的合集分支退化为只量模板（`if (true)`），新加的 4 条纯函数
用例立刻转红（`4 failed | 42 passed`），还原后全绿 —— 说明它们测的是判据，不是「代码跑得通」。
界面用例同理：`getByText("渲染后最长 …")` 找不到元素就会失败，不存在静默通过的空断言。

回归：前端全量 **20 个文件 / 264 passed**（`biliForm.test.ts` 35 → 46，`bili-upload.test.tsx`
64 → 68，`styles.test.ts` 3 → 4 —— 新增一条源文不变式：`.char-count.is-over` 必须存在且只改颜色/字重），
`tsc --noEmit` 通过。后端未改动。

## 风险与回滚

- **行为变化只有一条**：合集渲染后超限时会挡住提交。这正是服务端 `validate_collection` 本来就会
  拒的情况，所以**不会挡掉任何能成功的投稿**。
- **误挡的唯一来源是「渲染不一致」**：若前端重放的渲染与服务端 `bili_meta.render` 不同
  （占位符集合、补零位数），就可能出现「界面说超了、服务端说没事」。缓解：两处共用同一组入参，
  且预览与判据在同一文件里用同一个 `renderItemTitle`。
- **超限只报错、不截断**（与服务端同一条约定），输入框里仍是用户输入的完整内容。
- 回滚：`git revert <commit>`，纯前端改动，无数据迁移。

## 未覆盖 / 如实说明

- **「渲染后最长」那一行在真实浏览器里量过了**（41 条断言全过：颜色 `rgb(180,35,24)`、字重 700、
  宽高 > 0、改模板后按钮放开并退回灰字），但只量了**这一处**在 1440×1000 下的形态；
  窄窗口（例如 1024）下那一行会不会换行挤坏布局没有量。
- **没有在真账号上投一个「渲染后超限」的合集**去验证服务端那条 400 —— 代价是一堆不可撤销的
  稿件。本轮只保证「判据同源」，不保证平台行为与 018 实测的那条 80 上限完全一致。
- **`prefill.problems`（服务端预填时给的快照）与本地预检会同时显示**：用户改过模板之后，服务端
  那一条是**旧模板**的结论，两句话可能同时出现且措辞不同（服务端点名原始标题、前端点名渲染
  结果）。本轮没有去重 —— 去重就会把「服务端认为有问题」这一事实藏起来。
- **合规档的灰字不是 `.char-count` 里写的 `#657187`**：那一档被 `.field > span`（0,1,1）压掉，
  实测是 `#334155`。超限档（0,2,0）压得赢，所以不受影响；合规档改不改属另一处改动，
  已记进 [README 的未处理清单](README.md)第 26 条。

## 关联

- [018](018-a-long-file-name-looks-like-a-login-failure.md)：同一条 80 字符上限的另一面 ——
  018 是平台把**文件名**也算进这条账、且报错被折成「登录失效」；本条是**前端压根没量**会被拒的那个值。
- [012](012-preflight-contradicts-itself-and-the-server.md)：同一条原则（本地预检与服务端同源），
  那次的形态是「两句话自相矛盾」与「回显截断写死 12」。
- [016](016-subtitle-lost-while-the-video-is-in-review.md)：同一条教训 ——
  **界面上「没问题」不等于真的没问题**，只是这次没有数据被丢掉，代价是用户白跑一次提交。
