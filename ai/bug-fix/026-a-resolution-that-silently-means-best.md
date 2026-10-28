# 026 · 清晰度写法差一个字母，就静默下了最高画质

- 状态：已修复
- 提交：`fix(schemas): 清晰度取值归一化，无法解释的写法当场报错`
- 影响面：**任何**从 API 外部进入的清晰度取值 —— 请求体、设置里的「默认清晰度」、环境变量
  `YTDL_DEFAULT_RESOLUTION`。一个大小写（`1440P`）或漏掉的 `p`（`1080`）就足够。
- 严重度：中高。**没有任何提示**：任务照常成功，只是画质不是用户要的那个，而日志、界面、
  任务详情全都显示「正常」。用户唯一的发现途径是去看成品文件的分辨率。

## 问题

用户把清晰度写成 `1440P`（或 `1080`、`hd`、`1440p60`）时，下载**不会报错**，而是下**最高画质**。

实测（`format_selector` 的输入与输出）：

```text
'1440p'    -> height=1440   selector='bv*[height=1440][ext=mp4][vcodec^=avc1]+ba[ext=m4a][acodec^=mp4a]/…'
'1440P'    -> height=None   selector='bv*+ba/b'
'1080'     -> height=None   selector='bv*+ba/b'
'hd'       -> height=None   selector='bv*+ba/b'
'1440p60'  -> height=None   selector='bv*+ba/b'
```

`bv*+ba/b` 是「最佳可用」（best video + best audio）。也就是说：**大写一个 P，等于让用户改选「最佳可用」。**

## 原因

`DownloadOptions.resolution` 是一个**没有任何校验**的自由字符串：

```python
resolution: str = "1440p"
```

而下游两处都只认严格的小写 `<数字>p`：

1. `ytdlp_formats.format_selector()` —— 解析不出高度就 `return "bv*+ba/b"`，即**静默兜到最高画质**；
2. `resolution_decisions.should_look_for_fallback()` → `resolution_height()` 返回 `None` ——
   于是「连降级链路都不介入」，因为它认为这个取值不表达任何高度，没有「比它低」的定义。

两层叠起来的结果：**既不按用户要求做，也不告诉任何人它没做。** 这与本仓库其他缺陷
（[008](008-return-in-finally-swallows-the-real-error.md) / [010](010-unselectable-probe-raise-skips-the-fallback.md)）
同一主题 —— 代码什么都没做，而界面/日志看起来一切正常。

同一个 `<数字>p` 的解析在 `ytdlp_formats` 里写了两遍（`format_selector` 与
`single_file_format_selector` 各一份），`resolution_height()` 是第三份。三份各自 drift 是时间问题。

## 修复方案

1. **入口归一化 + 拒绝**（`schemas._normalize_resolution`）：去首尾空白、转小写；
   只接受 `best` 与 `<正整数>p`，其余抛出可照做的错误（API 层是 422）。
   归一化只做**无歧义**的改写，不做猜测 —— `1080`、`hd`、`1440p60` 各自可能意味着不同的东西。
2. **三个入口都要挡**：`DownloadOptions.resolution`、`RestartJobRequest.resolution`、
   `SettingsUpdate.default_resolution`。第三个必须单独加，因为重启走的是
   `JobManager._options_with_resolution()` → `model_copy(update=…)`，**pydantic 不会重新校验**；
   只在 `DownloadOptions` 上加，重启这条路会静默漏过去。
3. **纯函数层保留兜底但不再重复解析**：`resolution_height()` 成为「`<n>p` 怎么解析」的唯一实现，
   并且**大小写不敏感** —— 库里可能存着早于这次校验的行（`1440P`），它们以前被当成 `best`，
   现在按 1440 处理才对得起当初的输入。

### 被否决的方案

- **把 `1080` 猜成 `1080p`**：漏一个 `p` 确实像笔误，但 `1080` 也可能是别的单位的残留；
  猜对九次没人夸，猜错一次就是「用户要 1080、实际下 2160」，而这正是要消灭的症状。
- **只在 `format_selector` 里抛异常**：那时已经在下载线程里、任务已入队，用户看到的是
  「下载失败」而不是「你这个取值不对」，而且报错位置离输入太远。
- **让 `AppSettings.default_resolution` 也校验（非法就起不来）**：环境变量写错一个字就
  开不了机，用户没有任何线索。现在的行为是：`1440P` 被归一化后正常工作；真无法解释的取值
  会在建任务/存设置时得到 422，消息里点名三处可改的位置。

## 效果

| 输入 | 修复前 | 修复后 |
| --- | --- | --- |
| `1440P` | 下最高画质，无提示 | 归一化成 `1440p`，按 1440 选 |
| ` 720p ` | 下最高画质 | 归一化成 `720p` |
| `1080` / `hd` / `1440p60` | 下最高画质 | **422**，消息写明「只接受 best 或 <高度>p」并指出改哪里 |
| 重启时传 `hd` | 覆盖 job 选项，下最高画质 | **422**，原选项不变 |

## 未覆盖 · 如实说明

1. **老数据只救大小写这一种**：库里若存着 `hd` 这类取值（正常界面产生不出来），恢复出来的行为
   仍是 `best`。做数据迁移要先枚举出存活行，不值得为一个无法从界面产生的值写迁移脚本。
2. **`1440p60` 被拒**：yt-dlp 支持 `[fps=60]`，本工具目前不提供帧率选择。用户若真要 60fps，
   现在会看到一句明确的拒绝，而不是静默拿到 30fps 或最高画质。
3. **没有在真实下载上验「422 之后什么都不会发生」**：只验到响应码与库里的 `options_json` 未变。
4. **`format_selector` 的兜底仍然是 `best`**：它是纯函数，没有「错误」这个出口。挡住它的是入口，
   这条兜底由 `test_a_value_that_cannot_be_parsed_falls_back_to_best` 明确记录着。

## 验证

**A/B（把 `schemas.py`、`ytdlp_formats.py` 还原到修复前）**：

```text
10 failed, 31 passed   ← 修复前（含 test_resolution_input 的全部 6 条与 3 条纯函数用例）
41 passed              ← 修复后
```

失败的 10 条集中在两件事：`1440P` 拿不到 1440 选择器；非法取值没被拒（`hd` 得到 201 而不是 422）。
其中两条专门钉**重启路径**（整任务 / 单条），因为那是最容易被 `model_copy` 漏掉的地方。

**回归**：`test_resolution_decisions.py` 里原本用 `DownloadOptions(resolution="hd")` 造输入的三条
参数化用例改为 `model_construct` —— 它们验的是**判定层**对历史数据的容错，入口收紧后这种取值
只能这样构造。改完 31 条全绿。

**全量**：后端 **1138 → 1223 passed**（本轮新增 85 条）；`schemas.py` 覆盖率 100% → 99%
（唯一未覆盖的是「非字符串直接放行」那行，正常 API 不会走到）。

## 风险与回滚

- **回滚**：revert 本提交即恢复「自由字符串」。已归一化写入库的 `1080p` 在旧代码下同样有效，
  所以回滚不会损坏数据。
- **对老客户端**：此前能提交 `hd` 的调用方现在会拿到 422。本仓库只有前端一个调用方，
  它只会送 `best` 与 `<n>p`（`quality.ts` 的固定列表 + 分析结果的高度），所以实际影响面为零 ——
  这条在 `test_resolution_input.py` 里由「`1080P` 被接受」与「`hd` 被拒」两条一起钉住。

## 关联

- 与 [010](010-unselectable-probe-raise-skips-the-fallback.md) 同属「降级/选择」这条链：
  010 修的是「降级分支不执行」，这里修的是「连降级都不会被触发」。
- `ytdlp_formats` 此前**没有任何直接用例**，本轮一并补上（`tests/test_ytdlp_formats.py`，35 条）。
