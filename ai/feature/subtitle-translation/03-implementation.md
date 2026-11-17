# 实现：字幕翻译

按计划第 10 节分五步，每步独立验收、独立提交：

| 步 | 提交 | 内容 |
| --- | --- | --- |
| 1 | `346dbe9` | `align` / `provider` + 25 条纯领域用例 |
| 2 | `6da3b7c` | `local` provider + 设置三处同步 + 探针接口 + 30 条用例 |
| 3 | `7ad29e7` | 新表、JSON store、7 条路由、SSE、前端抽屉 + 12+7 条用例 |
| 4 | `7606085` | `zh` 译轨与 `en` 并存、只补字幕不重投 + 3+1 条用例 |
| 5 | 本次 | 全部对外文档同步 |

## 关键实现点

- **译轨调用链**：`attach_translated_subtitle` 与 `_attach_subtitle` 复用同一个
  `bili_subtitle.upload_subtitle`（端点、BCC、白名单只有一处），但前者不接
  `UploadTarget`/`UploadResult` —— 它可以在稿件早就发布完之后被调用。
- **注入纪律**：`create_app(translation_provider_factory=...)`；测试传假 provider 即声明
  "这次不联网"。默认工厂现场读可变的 `AppSettings`，所以改设置后下条请求就生效。
- **循环职责**：`service._run` 只处理分块、回填、落盘与进度；取消在块边界检查；
  最终状态按 `failed` 计数决定。
- **异常语义**：`TranslationError` 从 provider 一路带到 HTTP 409/200 结论，
  不把"模型暂时不可用"说成"翻译质量差"，也不把"没字幕"说成"失败"。

## 契约同步清单（逐条核对，不能只跑闸门）

`contracts/schemas.py` ↔ `frontend/src/contracts/types.ts` ↔ `docs/openapi.yaml`：
`SettingsRead/Update`、`TranslationProbeRead`、`TranslationRead`、`TranslationCueRead`、
`TranslationExportRead`、`TranslationCreateRequest`、`TranslationEditRequest`、`BiliUploadRead`。
`check_api_contract.py` 已把新增接口登记到 `SCHEMA_PAIRS`。
