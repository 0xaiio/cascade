# A-02　playlist 条目：按序号范围选、按标题筛、反选

来源：[`ai/plan/brainstorming.md`](../plan/brainstorming.md) §A-02。2026-11-11 落地，一项一个提交。

## 结论：后端早就有能力，缺的一直是入口

`CreateJobRequest.playlist_items` 本来就收任意序号列表（`api_support.create_job_records` 按 `entry.index` 匹配），所以这一整条只动前端：一个两行的工具条，加一份**纯判定**模块。没有新增接口、没有改请求体。

```
[全选 / 清空选择] [反选]  范围 [1-20,35,40-] [选中]
[筛选 ____] [清除筛选]                    已选 12 / 12（其中 6 条被筛选隐藏）
```

## 语义：只有一套，而且要写在界面上

候选池的风险栏写着「范围与勾选态的合并规则必须写清是『追加』还是『替换』」。这里选了**追加**，同时把另外两条同类约定一起定死 —— 三件事都不是实现细节，是用户会直接撞上的判断：

| 决定 | 为什么 | 界面上的证据 |
| --- | --- | --- |
| 范围**追加**，不替换 | 「替换」更符合「我选了 20–60」的直觉，但用户接着想补一段时就只能重来；追加最坏是多勾几条，再点一次就取消 | 按钮 `title` 写明「不取消已勾选的」；执行后回执「已加入 N 条（不会取消已选）」 |
| 筛选**只改显示** | 筛选是找东西的方式，不是选择操作；若它顺手改选择，用户会丢东西而不自知 | 计数行「已选 N / M（其中 K 条被筛选隐藏）」；空结果提示也重复这一句 |
| 批量动作只作用于**当前显示的行** | 否则「筛完再全选」会选中看不见的东西，与上一行自相矛盾 | 范围跳过隐藏条目时回执写「；K 条被筛选隐藏，未加入」 |

序号一律指条目表的 `#` 列（`VideoEntry.index`，从 1 开始），不是它在筛选结果里的位置。

## 范围解析

`parseIndexRanges(input, total)`：

- 分隔：半角逗号、全角逗号、空格；
- 单点 `5`、闭区间 `2-4`、开区间 `8-`（到末尾）/ `-3`（从第 1 条）；
- 端点越界**夹取**（`0-2` → 1、2），整段都在界外才算错（`11-20` 共 10 条）；
- 起点大于终点（`5-3`）、看不懂的 token（`abc`）、空输入 → `problems` 非空；
- **有 `problems` 时调用方整次丢弃**：不部分执行 —— 半个范围比一句报错更难查。
- 结果去重升序。

## 实现

- [`playlistSelection.ts`](../../frontend/src/download-request/playlistSelection.ts)：`parseIndexRanges` / `filterEntries` / `addIndices` / `removeIndices` / `invertIndices` / `areAllSelected`，全部纯函数，不改传入的 `Set`。
- [`AnalysisPanel.tsx`](../../frontend/src/download-request/AnalysisPanel.tsx)：两个局部状态（范围文本、筛选词）+ 两条回执（`playlist-error` / `playlist-note`）。选择本身仍归 `App` —— 提交任务要用同一份。
- `useEffect([analysis])`：换一份解析结果（包括对同一链接重新解析）就清空范围与筛选。否则新列表一进来可能被上一次的筛选藏掉大半，而用户看不到「为什么只剩 3 条」。
- 筛选输入有**可见标签**「筛选」：placeholder 在输入之后会消失，只剩一个来历不明的方框（第一版就是这样，截图时发现）。
- 样式：`.playlist-toolbar` 两行、每行 `flex-wrap`；两个输入各自收回固定宽度（全局 `input { width: 100% }` 会在 flex 行里各占一整行）。

## 顺带修掉的一处既有溢出（→ [ai/ui/012](../ui/012-analysis-panel-widens-the-page.md)）

390px 下解析一个 playlist，整页横向溢出 **282px**：`.analysis-panel` 是 grid item、默认 `min-width: auto`，而 `table { min-width: 620px }`（主栏宽表的既定规则）把它整列撑到 654px。用**旧产物**（A-02 之前）复测同样 282px，所以不是这次引入的；修法是一行 `.analysis-panel { min-width: 0 }`，超出的部分交给 `.table-wrap` 自己横向滚动。修复后同场景 0px。

## 验证

| 层 | 结论 |
| --- | --- |
| [`playlistSelection.test.ts`](../../frontend/src/download-request/playlistSelection.test.ts) | 13 条：四种范围写法、全角逗号与空格、去重升序、越界夹取与整段越界、`5-3` / `abc` / 空输入 / 空列表、筛选的大小写与空查询（返回同一数组）、追加/移除/反选/全选判定、纯函数不改入参 |
| [`playlist-selection.test.tsx`](../../frontend/src/download-request/playlist-selection.test.tsx) | 6 条：范围追加不取消已选、反选连点两次往返、筛选后全选只作用于显示行且计数写明隐藏条数、范围在筛选下跳过隐藏项、写错整次不生效且改输入即收回、换解析结果后筛选与范围清空 |
| CDP（`tmp_acceptance/ui_playlist_toolbar.py`） | 1440：工具条 924px 一行放得下，页面溢出 0；390：面板 354px、工具条 320px 折成 4 行、7 个控件**无一越界**、页面溢出 0（修复前 282px）。截图 `tmp_acceptance/a02-playlist-toolbar-{desktop,mobile}.png` |
| [styles.test.ts](../../frontend/src/styles.test.ts) | +1 条源文不变式：`.analysis-panel` 必须 `min-width: 0`、`.table-wrap` 必须 `overflow-x: auto`。**承重**：删掉前者，该条立刻红（`expected '\r\n' to match /min-width://s*0/`）；恢复后 11 passed |
| 全量 | 前端 **37 文件 / 435 passed**（串行）、`tsc --noEmit` 通过、`vite build` 通过 |

## 遗留

- 筛选**不持久化**（换解析即清空）——这是有意的：它属于「这一份列表」的临时视图，存下来会在下一次解析时静默藏条目。
- 范围不支持奇偶步长（`1-20/2`）与排除（`1-20,!5`）。真实需求是先覆盖「一大段 + 几个单点」，这两样等有人提再用；语法每加一项，报错文案就多一分解释成本。
- 表格自身在 390px 下仍横向滚动（`min-width: 620px` 是主栏宽表的既定设计），本轮只保证**页面**不被撑宽。
