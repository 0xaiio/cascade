# 040 · 新列没登记迁移，服务能打印地址却起不来

**症状**：按一键脚本启动，终端显示

```
==> 单端口模式：启动后端（默认带 --auto-port 自动选择可用端口，并自动打开浏览器）
==> 已自动选择端口 8001，正在打开浏览器 http://127.0.0.1:8001 ...
```

浏览器却显示 `127.0.0.1 拒绝连接`；`netstat` 看不到 8001 在监听。

## 根因：两处新增列没进迁移登记

`data/logs/app.log` 的最后两行给出了真正的原因：

```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such column: bili_upload.translated_subtitle_state
2026-11-17 20:50:42 ERROR uvicorn.error | Application startup failed. Exiting.
```

模型里 `BiliUpload` 的三个译轨列（`translated_subtitle_state` / `_lan` / `_message`）
与 `SubtitleTranslation.display_mode` 是在**表已经建好之后**才加的。项目本来就有追加式
补列机制 `infra/db._ensure_columns`，但这次加列时只改了 `contracts/models.py`，
忘了在 `additions` 里登记。`SQLModel.metadata.create_all()` 对**已存在**的表什么也不做 ——
它不会给老表加列。

启动时 lifespan 里的孤儿恢复要查 `bili_upload WHERE status IN (...)`，SELECT 列表包含新列，
于是 **建表成功 → 启动崩溃**。这也解释了为什么地址能打印出来：地址是 uvicorn 绑定端口时
打印的，早于 lifespan；用户于是看到一个"已经起来了"的假象。

对真实库做全量比对，漂移正好两处（其余表一致）：

| 表 | 缺列 | 来自哪一步 |
| --- | --- | --- |
| `bili_upload` | `translated_subtitle_state` / `_lan` / `_message` | 译文字幕作为独立 `zh` 轨 |
| `subtitle_translation` | `display_mode` | 中英双语同屏 |

## 为什么测试全绿

后端当时 **1519 passed**，但没有一条用例走过"老库升级"路径：所有测试都从
`create_all` 建全新库，新列自然齐全。`test_db.py` 里原有的
`test_init_db_adds_columns_that_existing_databases_are_missing` 也只是先 `init_db`
再从零检查列，**并没有真的先建一张旧形状的表**。

## 修法

1. `infra/db._ensure_columns` 登记四列，默认值对齐历史事实：
   - 译轨三列默认 `skipped` / `''` / `''` —— 这些老稿件当时根本没有译轨这一步，
     标 `failed` 会让它们凭空多出一个失败结论（与当年 `subtitle_state` 的取法一致）；
   - `display_mode` 默认 `'zh'` —— 老翻译任务创建时只有"仅中文"一种呈现。
2. `test_db.py` 新增 `test_upgrade_adds_translated_subtitle_columns_and_display_mode`：
   **手动先建缺列的旧表并插入老行**，再 `init_db`，既断言列补齐，也断言老行拿到的是
   `skipped` / `''` / `'zh'` 而不是失败态。
3. `init/start.ps1` 与 `init/start.sh` 在打开浏览器前检查后端进程是否还活着。
   否则任何 lifespan 失败都会被呈现成"正在打开浏览器 + 拒绝连接"，真正原因被埋在日志里。

## 验收

- 变异测试：临时禁用 `_ensure_columns(engine)`，新用例当场红在
  `no such column: translated_subtitle_state`；恢复后绿。
- 用真实库 `data/app.sqlite3` 跑 `python -m app --auto-port`：
  `Application startup complete` / `Uvicorn running on http://127.0.0.1:8001`；
  `GET /api/jobs` 与 `/api/settings` 均 200，根路径返回前端页面。
- 真实库补列后复核：四列全部存在；老行默认值正确。

## 复发判据（加列时必查）

**模型加列 = 三处同步**：`contracts/models.py` + `infra/db.py` 的 `additions` + 契约
（`schemas.py` / `types.ts` / `openapi.yaml`，若该列对外可见）。新表由 `create_all` 建全，
不需要登记；**已存在的表补列必须登记**。判据不是"测试过了"，而是"用一张旧形状的表
跑过 `init_db`"。

相关：[035](035-diagnostics-shows-the-stored-value.md)（同一主题：真正的原因不在用户看得见的地方）。
