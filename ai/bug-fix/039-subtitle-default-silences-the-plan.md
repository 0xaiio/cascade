# 039　默认字幕语言把「按主语言挑字幕」整条路堵死了

来源：[`ai/plan/brainstorming.md`](../plan/brainstorming.md) §A-03（状态「待取证」）。2026-11-11 取证并修复。

## 问题

设置里的默认字幕语言（出厂 `["en"]`）在**每次解析**时被原样放进下载选项。于是：

- 视频没有英文轨时，界面写「已选 1 项：en」，而下拉列表里没有 `en` 的复选框 —— **摘不掉**
  （[`ai/ui/README.md`](../ui/README.md) 候选 1，实测复现过）；
- 更严重的是**下游**：这个非空列表会让后端**绕过**「按视频主语言挑一条」的字幕计划，
  yt-dlp 拿一个视频里不存在的语言去匹配 → 一条轨都匹配不到 → **静默不下字幕、也不报错**，
  任务照样 `succeeded`。

也就是说：**任何非英文视频，在默认设置下都不会下到字幕**，而界面从头到尾没说过这件事。

## 取证（先证实，再改）

### 1. 后端对「不存在的语言」是静默跳过，不是失败

`job_manager._options_with_subtitle_plan`：

```python
plan = getattr(preparation, "subtitle", None)
if plan is None or options.subtitle_languages:
    return options          # ← 用户给了语言：计划被整段绕过
return options.model_copy(update={"subtitle_languages": [plan.code or plan.language]})
```

`subtitle_plan.py` 的模块注释把另一半写死了：「该语言一条都没有时 yt-dlp 用 `fullmatch`
匹配不到任何轨，`requested_langs` 为空 → 什么都不下、**也不报错**」。

新增用例把这条链钉住（`backend/tests/test_subtitle_recording.py`）：

```
test_explicit_language_bypasses_the_plan_even_when_the_video_lacks_it
  options.subtitle_languages = ["en"]，计划是 zh-Hans
  ⇒ 服务层真的收到 ["en"]
  ⇒ 任务 succeeded
  ⇒ 磁盘无字幕文件，subtitle_source = none
```

### 2. 影响面

默认设置 `["en"]` + 一个中文视频 = 每次都没有字幕。这不是「边缘情况」：
本项目面向中文用户，中文视频占相当比例。

## 修法：在前端把默认值收敛到「这个视频真有的语言」

```ts
export function effectiveSubtitleLanguages(analysis, preferred) {
  const available = new Set([...analysis.subtitles, ...analysis.automatic_subtitles].map((s) => s.language));
  return Array.from(new Set(preferred)).filter((code) => available.has(code));
}
```

- 命中 → 照旧生效（有英文轨的视频行为不变）；
- 一个都不命中 → `[]`，把选择权交回后端的字幕计划（按主语言挑一条，人工优先、无则 ASR）；
- 空列表在界面上现在是 **「自动（按视频语言）」**，并写明「未选择时按视频主语言自动挑一条
  （中 / 英），人工字幕优先」。写「未选择」会被读成「不下字幕」，与事实相反。

**为什么不在后端兜底**：后端拿到的只是一个语言列表，区分不了「用户显式选了 en」与
「前端把默认值原样塞进来」。替用户改语言会让显式选择失效 —— 而显式选择在多轨视频
（同一视频既有英文又有中文）上仍然是有用的。**只有界面知道这个视频有哪些轨**，收敛必须发生在那一边。

**为什么不干脆删掉语言选择器**：删掉等于放弃「多轨视频里只要其中一条」的能力，
也解释了不了一条已经存在的设置项为什么消失。收敛保留了两者：默认值不再说谎，
显式选择仍然优先。

## 效果

| 场景 | 改前 | 改后 |
| --- | --- | --- |
| 视频只有 `zh-Hans`，默认 `["en"]` | 标签「已选 1 项：en」（摘不掉）；提交 `["en"]`；**无字幕** | 标签「自动（按视频语言）」；提交 `[]`；后端挑 `zh-Hans`，**有字幕** |
| 视频有 `en`，默认 `["en"]` | 标签「已选 1 项：en」；下英文字幕 | 不变 |
| 视频只有 `zh-Hans`，用户手动勾 `zh-Hans` | —— | 标签「已选 1 项：zh-Hans」；提交 `["zh-Hans"]`（显式优先） |

## 验证

| 层 | 结论 |
| --- | --- |
| `frontend/src/shared/subtitles.test.ts` | 5 条：命中原样保留（人工或自动都算）、不命中返回空、多语言保序去重、空偏好返回空、不改入参 |
| `frontend/src/download-request/subtitle-language-default.test.tsx` | 3 条：中文视频不再假装选着 en（标签为「自动」、下拉里没有 en、提交 `[]`）、有 en 时默认照旧、收敛后仍可手动显式选一条并提交 |
| `backend/tests/test_subtitle_recording.py` | +1 条取证：显式 `["en"]` 绕过计划、任务成功但无字幕 |
| 变异验证 | 把 `applyAnalysisResult` 换回收敛前的写法（默认值原样塞进选项）：`subtitle-language-default.test.tsx` **2 failed / 1 passed** —— 中文视频那条与「收敛后手动显式选一条」那条转红；恢复后 8 passed（含 5 条纯函数）。说明这两条咬的是收敛本身，不是标签文案 |
| 全量 | 前端 **39 文件 / 443 passed**、后端 **1429 passed**、`tsc --noEmit` 通过 |

## 风险与回滚

- 行为变化：默认设置下，非英文视频现在**会**下到字幕（以前不会）。这是修的目的；
  若有人依赖「默认不下字幕」，把设置里的默认语言清空即可（那时也是自动，语义相同）。
- 已经建好的任务不受影响：它们的 `options_json` 里已经固化了当时的语言列表。
- 回滚：`applyAnalysisResult` 里把 `effectiveSubtitleLanguages(...)` 换回
  `settings?.default_subtitle_languages ?? ...`；空态标签与说明一并回退。

## 关联

- [A-02](../feature/A-02-playlist-selection.md)：同属「界面上的选择必须真的生效」类问题。
- [ai/ui/README.md](../ui/README.md) 候选 1：本条修的就是它，索引里已标注已修。
- [ai/bug-fix/014](014-a-dead-notify-switch.md)：同一个教训的另一个实例 —— **控件存在 ≠ 功能存在**。

## 未覆盖

- 「字幕来源」与语言收敛的交叉场景（例如视频只有自动中文字幕、来源选了「只要人工」）
  仍按既有逻辑走：计划会给出 `none` 且不报错，界面在下载后如实显示「无字幕」。本次未改动这条。
- 没有做真实账号下的端到端复验（需要一只非英文视频 + 真实下载）；判据是代码路径与两层用例。
