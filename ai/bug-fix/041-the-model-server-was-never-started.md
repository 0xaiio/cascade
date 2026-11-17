# 041 · 译为空按钮：模型服务没起，用户只看到 WinError

**症状**：点任务中心的「译为中文」或投稿页的同一个入口，抽屉里报

```
连不上本机模型服务：<urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>
```

原文一条都没译出来。查 `netstat` 可见 8080 无监听，但 `D:/models/Qwen3-8B-Q5_K_M.gguf` 在、PATH 里也有 `llama-server` —— 上一轮收尾时把模型服务停掉了，之后没人再拉起它。

## 根因有两层

1. **环境层**：翻译要求本机先跑一个 OpenAI 兼容服务，但应用对此**既不放也会**，完全依赖用户手动准备。机器重启、关过一次窗口、上一轮验证收尾停进程，都会让翻译按钮变成必然失败的入口。
2. **产品层**：失败结论是 `str(URLError)` 原文 —— 一个 `WinError 10061`。它既没说「服务没起」，也没说下一步该做什么；而且同一个错误在每条 cue 上各出现一次，把一条环境故障伪装成 N 条翻译质量问题。

这和 [038](038-browser-cookie-import-is-gone.md) 是同一类错误：**一个必然失败的入口**，区别是这次能力本身有价值，缺的是把它接通的那一步。

## 修法

新模块 `translation/model_server.py`，在 `TranslationService._run` 发任何请求之前调用：

- 先 GET `/health`；已就绪直接复用（不重复起进程）。
- 否则用 PATH 中的 `llama-server`（或 `YTDL_TRANSLATION_SERVER`）+ 本机已有 GGUF 拉起：
  只绑 `127.0.0.1`、端口取自已校验为 loopback 的设置、不用 `shell=True`、进程 detached、日志写 `data/logs/model-server.log`。
- 本机实测 Vulkan 后端 12–17 秒加载完 8B；Vulkan 进程退出则自动回退 CPU 再试一次。
- 仍失败时抛 `ModelServerError`，**一条模型请求都不发**：任务 `error` 与每条 `cues[].error` 是同一句可操作结论（缺模型 / 缺可执行文件 / 看日志路径）。

`local._http_error_message` 同时把「连接被拒」从原始 `WinError` 翻成「本机模型服务没有运行 + 下一步」，兜住服务启动后又崩的边界。`create_app` 新增可注入的 `model_server_ensurer`：测试传 `lambda: None` 即声明「模型由我接管」，与 `provider_factory` 的注入纪律一致，单测不会在用户机器上真起服务。

前端在 `translated=0 / failed=0 / running` 时显示「正在准备本机模型服务，首次启动约需十几秒…」——0/N 不再是一个让人猜的静默状态。

## 验收

- **新用例**：`test_model_server.py` 6 条（复用、缺模型/缺二进制、Vulkan 优先、CPU 回退、失败文案不含 WinError、启动命令只绑 loopback）；`test_translation_local.py` +1（10061 译文）；`test_translation_api.py` +2（服务失败时 `provider.calls == []` 且结论统一 / 翻译前确实调用 ensure）；前端 +1（准备中的提示）。
- **变异测试**：把 `startup_note = self.ensure_server()` 改成 `None`，两条端到端用例当场红；恢复后绿。
- **真机**：从「8080 无监听」起，`ensure_model_server()` 17.6 秒拉起服务、`/health` 200；同一进程内真实翻译
  `We begin with the fundamentals of query execution.` → `我们从查询执行的基础开始。`（11.2 秒）。

## 边界

- 沙箱/临时 shell 里的 detached 子进程会随会话被回收；真实用户从一键脚本启动应用时不受影响，服务进程独立存活，下次点翻译直接复用。
- 自动发现只覆盖 `D:/models`、`~/.cache/llama.cpp`、`~/.lmstudio/models` 与 `YTDL_TRANSLATION_MODEL_PATH`；模型不在这些位置时要按错误提示设置路径。
- 自动启动的是本机已有服务，不下载模型、不装依赖、不连外部网络；安全边界见 `docs/safety-review.md`。
