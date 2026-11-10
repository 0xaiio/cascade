# 390px 下解析合集会把整页撑宽 282px（表格的 620px 由页面承担，而不是自己滚动）

## 问题

窄屏（390×844）解析一个 playlist 之后，**整页出现横向滚动条**：内容宽 672px，溢出 282px。
所有面板一起变宽（解析面板、任务卡、设置面板都是 654px），标题被裁在屏幕外，必须左右拖动才能读。

实测（`tmp_acceptance/ui_playlist_toolbar.py`，合成 12 条合集）：

| 量什么 | 数值 |
| --- | --- |
| 视口 | 390 |
| `documentElement.scrollWidth - clientWidth` | **282px** |
| `.analysis-panel` 宽度 | **654px**（应为 354） |
| `.table-wrap` 宽度 / `scrollWidth` | 620 / 620（没有内部滚动） |
| `table` 宽度 | 620 |

## 原因

两个既有声明叠在一起：

1. `table { min-width: 620px }`（`styles.css`）是**主栏宽表**的既定规则 —— 表格不允许被压到 620 以下。
2. `.analysis-panel` 是 `.primary-column`（grid）的 item，默认 `min-width: auto`，**不允许被压得比内容窄**。

于是表格的 620 变成整列的 min-content：列宽 → 654（620 + 面板左右内边距 34），页面跟着变成 672。
`.table-wrap { overflow-x: auto }` 本来是为了让表格自己滚，但它只有在**容器先被压窄**时才生效 ——
容器被 min-content 撑着，永远不会出现溢出可滚。

**不是 A-02 引入的**：用 A-02 之前的产物（`tmp_acceptance/dist_settings`，同一套合成数据）复测，
同样 `overflow: 282, panel: 654, table: 620`。A-02 只是让这条路径更容易被走到（工具条本身 320px，不越界）。

## 修复

```css
/* styles.css */
.analysis-panel {
  min-width: 0;
}
```

一行，把「grid item 不许被压窄」这条默认解除，min-content 不再参与列宽计算；超出的部分落回
`.table-wrap` 的 `overflow-x: auto`。

**为什么不改 `table { min-width }`**：那条规则服务的是主栏里的宽表（B站 上传预览表另有覆盖，
`docs/design.md` 有记录）。把 620 调小会让宽表在桌面端挤成多行，是另一个决定；这里要修的只是
「谁承担溢出」——应由表格容器承担，而不是整页。

**为什么不给 `.table-wrap` 加 `min-width: 0`**：它需要的不是最小宽度，而是**父级允许它变窄**；
`.panel` 的 `min-width: auto` 才是卡点。

## 效果

同一页面、同一份合成数据，390×844：

| 量什么 | 改前 | 改后 |
| --- | --- | --- |
| 整页横向溢出 | 282px | **0px** |
| `.analysis-panel` | 654px | **354px** |
| `.table-wrap` 宽度 / `scrollWidth` | 620 / 620 | **320 / 620**（内部滚动） |
| `table` | 620px | 620px（不变，按设计） |

工具条本身在两档宽度下都无越界：1440 下 924px 一行；390 下 320px 折成 4 行、7 个控件全部落在面板内。

## 验证

- **CDP 探针**（`tmp_acceptance/ui_playlist_toolbar.py`）：1440 与 390 各量一次整页溢出、面板宽、工具条宽、
  每个控件的 `left/right` 与面板边界；两次都是 0 溢出、0 越界。截图
  `tmp_acceptance/a02-playlist-toolbar-{desktop,mobile}.png`。
- **旧产物对照**：`dist_settings`（A-02 之前的构建）在 390 下同场景溢出 282px，证明这是既有缺陷。
- **样式源文不变式**（`frontend/src/styles.test.ts`）：`.analysis-panel { min-width: 0 }` 与 `.table-wrap { overflow-x: auto }` 都被钉住；删掉前者该条立刻红。它挡不住「换一种写法又顶宽」，但能挡住这条声明被顺手删掉。
- 单测覆盖不到布局本身：jsdom 不做布局，像素判据只能靠上面的探针（见 `ai/ui/README.md` 的「自动化覆盖边界」）。

## 风险与回滚

- 改的是 `.analysis-panel` 一个类，只影响解析结果面板；其它面板在 390 下本来就不会被这条规则影响
  （它们没有 620px 的表格）。
- 回滚：删掉那一条声明即可，页面回到「整页横向滚动」。

## 关联

- [A-02](../feature/A-02-playlist-selection.md)：在验证批量选择工具条的窄屏表现时发现并顺手修掉。
- [002](002-side-column-overflow-breaks-the-page.md) / [003](003-language-trigger-label-overflows-the-page.md)：
  同一族问题 —— **某个子元素的 min-content 把整页顶宽**。前两条在右栏（390px 固定轨道），
  这一条在主栏（grid item 的 `min-width: auto`），判据相同：谁需要 620px 的宽度，就由谁自己滚。

## 未覆盖 / 如实说明

- 只量了 390 与 1440 两档。681–1023px 之间（单列、但比手机宽）没有单独量，理论上只会更宽松。
- 表格在 390px 下仍然要横向滚动才能看全标题 —— 这是 `min-width: 620px` 的既定设计，本轮不改变它，
  只保证**页面**不再跟着一起滚。
- 没有改 `table` 的列宽策略（例如窄屏隐藏「时长」列）：那属于表格自身的信息取舍，需要单独决定。
