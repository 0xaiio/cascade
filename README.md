# YouTube Downloader

YouTube Downloader 是一个本机单用户下载控制台：FastAPI 后端负责调用 `yt-dlp`、维护 SQLite 任务状态和推送下载进度，React/Vite 前端提供链接解析、下载选项和任务中心。

请只下载你拥有权利或已获得许可的内容。本项目不实现 DRM 绕过，也不面向公网部署或多用户权限场景。

## 快速启动

```powershell
cd backend
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
cd frontend
npm install
npm run dev -- --port 5173
```

打开 `http://127.0.0.1:5173`。完整安装、配置和运行说明见 [开发文档](docs/development.md)。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [文档总入口](docs/index.md) | 按读者角色选择阅读路径。 |
| [用户手册](docs/user-manual.md) | 安装、运行、下载操作、cookies 和常见排障入口。 |
| [需求分析](docs/requirements.md) | 项目目标、功能需求、非功能需求和边界。 |
| [架构设计](docs/architecture.md) | 前后端、SQLite、yt-dlp、ffmpeg、SSE 和外部依赖关系。 |
| [开发文档](docs/development.md) | 环境准备、依赖安装、配置项、目录结构和运行命令。 |
| [API 文档](docs/api.md) | HTTP endpoint、请求响应模型、任务状态和诊断字段。 |
| [技术文档](docs/technical.md) | 清晰度/格式选择、降级策略、cookies、PO token 和稳定下载策略。 |
| [实现文档](docs/implementation.md) | 核心模块职责、任务调度、进度聚合、读模型和数据库补列。 |
| [测试文档](docs/testing.md) | 自动测试、手动验收和回归重点。 |
| [维护文档](docs/maintenance.md) | 文档同步规则、变更 checklist 和排障流程。 |

## 测试摘要

```powershell
python -m compileall backend\app
python -m pytest backend\tests -q
cd frontend
npm test
npm run build
```

## 维护约定

功能、命令、配置、API、架构、下载策略或测试方式变化时，必须同步更新 `docs/` 中的对应文档和 UML 图。README 只作为入口页，不承载详细设计内容。
