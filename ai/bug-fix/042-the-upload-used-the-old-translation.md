# 042 · 改完译文直接上传，B站 挂上去的是旧的那一份

## 结论

最近三条字幕链（翻译、双语、上传闭环）的集成处有五处缺陷，共同形状是
**「一处状态改了，另一处仍拿着旧的产物」**：

1. 人工修订译文后直接上传 —— 挂的是编辑前的 SRT（**数据错，界面还说成功**）；
2. 双语的人工核对 SRT 与上传 SRT / BCC 排版不一致（一份两行、一份四行以上）；
3. 双语导出拒绝空译文时，错误信息指向另一层的判据，让人以为漏填了原文；
4. 本机模型服务 Vulkan 启动失败后回退 CPU：没等旧进程退出就抢同一端口，
   两条路都失败时回退进程还留在后台；
5. 翻译抽屉首次打开只有一句普通小字，用户不知道该等还是该再点一次。

其中 1 是真缺陷，2/4/5 是「界面说对了、产物或状态不对」的同类问题，3 是死代码造成的误导。

## 1 · 改完译文上传的是旧文件

### 证据

`test_editing_a_cue_then_uploading_must_not_send_the_stale_export`（红灯 → 修复后绿灯）：

```
翻译完成 → export（落盘） → PATCH /cues/0 改成「人工修订的译文」
→ POST /upload-bili → fake_attach 收到的文件内容仍是「译文1」
```

`edit()` 只改 `data/translations/<id>.json` 的逐条记录，不动 `row.output_path` 指向的 SRT；
`upload_translated_subtitle` 直接把这个路径交给 `attach_translation`。于是
**HTTP 200、`translated_subtitle_state=posted`、平台挂的是旧译文** —— 没有任何异常可查。

这与 [033](033-the-response-keeps-counting-after-success.md) 同族：
一侧已经变了，另一侧还在用变更之前的快照；区别是这次错的是产物而不是读模型。

### 修法

服务端不再信任 `row.output_path`：`POST /api/translation/jobs/{id}/upload-bili`
在挂轨之前**按当前 cues 重新导出**（三个小文件，成本可忽略），拿新的
`srt_path` 去上传。「还没导出所以 409」这条前置条件同时去掉 —— 上传本来就不该
以用户手动点过导出为前提。

前端配合：`exportStale`（原名 `modeDirty`，只覆盖切模式）扩成「导出内容已过期」，
编辑译文成功后也置位；提示文案统一为「内容已变更，上传前会自动重新导出」，
与上传按钮的 title 一致。原先「切模式后必须重新导出，否则界面会挡住上传按钮」
的文档描述与实现不符（按钮从未被挡住），一并纠正。

## 2 · 双语三份产物的排版必须一致

`bilingual_content` 会把原文与译文各自的内部换行压成空格，上传 SRT 与 BCC 因此
恰好两行。但 `render_bilingual_srt`（给用户核对的 `*.bilingual.srt`）直接拼
`cue.source` + `cue.translation`：源 VTT 的多行、`wrap_chinese` 断出的行都会留着，
一条 cue 变成四行以上。**同一个「双语」在三个文件里长得不一样**，核对就失去意义。
改为三份产物共用 `bilingual_content`。

## 3 · 空译文的错误信息要指向那一条

`export_bilingual_files` 在 `validate_alignment` 之后又遍历所有 cue 调了一次
`bilingual_content`，返回值没人用；理论上会先抛出「需要原文和译文都不为空」，
把「第 N 条没译完」说成「原文可能漏填」。实际路径里 `validate_alignment` 先拦住了
（所以 A/B 中这条用例本来就是绿的），属于死代码清理，不是行为修复 —— 如实标注。

## 4 · 模型服务回退时的进程清理

`ensure_model_server` 先试 Vulkan0，失败则 `terminate()` 后立刻用同一端口起 CPU 回退。
**terminate 不等于退出**：不等 `wait()` 就抢端口，回退进程会撞上地址占用；
两条路都失败时，回退进程也没清理，下次点翻译会重复撞它。
新增 `_stop()`：terminate 后 `wait(timeout=5)`（异常只记 DEBUG，不挡回退），
两条路都失败时也停掉回退进程。

## 5 · 首次等待要看得见

抽屉首次打开（`job === null`）原本是 `<p className="translation-hint">正在准备字幕…</p>`，
而同一文件里已有转圈 + 加粗 + 「无需任何操作」的 `translation-booting` 状态条。
首次要等模型服务十几秒，普通小字会被当成「按钮没生效」。改为复用醒目状态条。

## 验证

新增 `backend/tests/test_subtitle_regressions.py`（6 条）与前端 2 条 UI 用例：

| 用例 | 修复前 |
| --- | --- |
| 改译文后上传 | 红（挂旧文件） |
| 双语核对 SRT 两行 | 红（四行以上） |
| 端到端：翻译 → 编辑 → 切双语 → B站 假端点 | 红（平台收到旧译文） |
| Vulkan 回退进程清理 | 红（terminate 没 wait，回退进程残留） |
| 前端：编辑后上传前重导 | 红（跳过 export） |
| 前端：首次等待用醒目状态条 | 红（普通 hint、无 `role=status`） |

另两条在修复前就是绿的，保留作回归守护：**进度事件的 `job_id`/`item_id`**
（我怀疑过 `Session` 外访问 row 会 DetachedInstanceError，实测属性已加载，假设证伪）、
**空译文错误信息**（死代码清理，行为未变）。

端到端边界：真机验收需要拉起本机 8B 模型跑满一轮（约 3.8 token/s），并真的改动
B站 稿件，不适合放进测试套件。替代方案是**真 HTTP + 真 DB + 真文件 + 真上传编排 +
假 provider + 假平台 transport**：`test_end_to_end_edit_and_bilingual_switch_reach_bili_as_two_lines`
走完路由 → 翻译 → 编辑 → 模式切换 → 重导 → `BiliManager.attach_translation` →
`upload_subtitle` 拼表单，断言 B站 表单收到「一条 cue、一个时间轴、两行，第二行是改后的译文」。

全量：后端 **1547 passed**（原 1541 + 6）、前端 **466 passed / 41 文件**（原 464 + 2）、
`tsc` 0、覆盖率 91% → **92%**（`render.py` 93% → 97%）、六道闸门全绿。

## 顺带修掉的测试污染

`test_translation_api.setup_job` 原来用 read model 的相对 `output_path` 派生字幕路径，
于是 `export` 会把 `1.en.zh.srt` / `.bilingual.bcc.json` 等五个文件写进 `backend/` ——
跑一次测试就在工作区留下未跟踪文件。夹具改为 `tmp_path` 下的绝对路径；
新增用例也自带 `_relocate_subtitle_into_tmp`。修完 `git status` 干净。

## 遗留

- 双语导出仍写三份文件（上传 SRT / 核对 SRT / BCC），没有去重；重复导出的开销可忽略。
- 模型服务的模型选择是「D:/models 下按文件名排序的第一个 GGUF」，
  多模型机器可能挑到非预期模型；可用 `YTDL_TRANSLATION_MODEL_PATH` 指定。
- `_stop()` 的 `wait(timeout=5)` 超时后只记 DEBUG 并继续；极端情况下残留进程仍需人工清理。
- 真机 B站 最终呈现（同 lan 覆盖后平台缓存多久刷新）未在真实账号复验。
