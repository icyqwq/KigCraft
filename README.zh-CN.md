# KigCraft

中文 | [English](README.md)

<p align="center">
  <img src="frontend/public/logo.png" alt="KigCraft logo" width="160" />
</p>

KigCraft 是一个用于制作 Kigurumi 头壳预览图的网页工具。它按实际制作沟通里的流程组织功能：上传角色参考图，确认需要保留的头部细节，生成正视图，编辑正视图，再生成四视图。

开发者：SeaRabbit / 海兔  
用户交流群：QQ 934715528

![KigCraft 编辑器界面](docs/images/editor-workspace.png)

## 功能

- 上传角色参考图，并补充一句需要保留的重点。
- 在生成前确认可编辑的角色细节列表，包括发型、眼睛、表情、耳朵和头饰。
- 通过可替换的生成 provider 生成正视图和四视图预览。
- 在编辑器里调整标注、landmark、脸型、眼睛、嘴巴、液化和局部生成。
- 支持中文、英文和日文界面，并可根据浏览器语言选择默认语言。
- 使用 Docker Compose 启动本地开发栈，包括 FastAPI、React、Postgres、Redis 和 MinIO。

## 许可证

KigCraft 使用 GPL-3.0-or-later 发布。详见 [LICENSE](LICENSE)。

## 环境要求

- Docker Desktop 或带 Compose 的 Docker Engine
- 只开发前端时需要 Node.js 22 或更新版本
- 只开发后端时需要 Python 3.12 或更新版本
- 使用 `GENERATION_PROVIDER=codex` 时需要已登录的 Codex CLI 配置

## 快速启动

```powershell
Copy-Item .env.example .env
# 对外提供服务前，请先修改 .env 里的所有 change-me-* 值。
docker compose up --build
```

使用 Codex 生成前，请在仓库根目录自行创建 `ref/` 并放入成品参考图。详见[成品参考图](#成品参考图)。

本地地址：

- 前端：<http://localhost:15173>
- API 健康检查：<http://localhost:18000/health>
- MinIO 控制台：<http://localhost:19001>

## 生成 provider

`GENERATION_PROVIDER=codex` 会在后端容器里调用 Codex CLI。运行时需要挂载已经登录的 Codex 配置目录：

```powershell
Copy-Item -Recurse "$env:USERPROFILE\.codex" ".\runtime\codex-home"
docker compose up --build
```

Linux 服务器上可以把已登录的 Codex 配置目录复制到主机，然后设置：

```dotenv
GENERATION_PROVIDER=codex
CODEX_PATH=codex
CODEX_CONFIG_DIR=/home/deploy/.codex
CODEX_PRODUCT_REFERENCE_PATH=ref/product-reference.png
```

### 成品参考图

仓库里不包含成品参考图。使用 Codex 生成前，需要自己在仓库根目录创建 `ref/` 目录，并放入以下文件：

| 文件 | 用途 |
| --- | --- |
| `ref/product-reference.png` | 正视图生成用的成品风格参考 |
| `ref/turnaround-reference.png` | 四视图生成用的成品风格参考 |

请使用 PNG 格式，并保持文件名一致。

这些图片用于约束生成结果的成品头壳风格，例如白底棚拍、材质、假发质感、构图和打光。它们与用户在界面上传的角色参考图不是同一类文件。

`ref/` 已加入 `.gitignore`，参考图会保留在本地，不会被提交到 Git。

```powershell
New-Item -ItemType Directory -Force ref
# 把你的参考图复制到 ref/ 目录
```

如需自定义正视图参考图路径，可在 `.env` 中修改：

```dotenv
CODEX_PRODUCT_REFERENCE_PATH=ref/product-reference.png
```

`GENERATION_PROVIDER=codex_bridge` 用于让 Codex CLI 在后端容器外运行。启动方式：

```powershell
.\tools\start_codex_bridge.ps1
```

非本地开发环境请设置自己的 `CODEX_BRIDGE_TOKEN`。

Fixture 和 mock 生成只用于测试或本地 smoke run，不要在生产环境启用。

## 部署

部署前先在服务器上准备生产 `.env`。至少需要设置：

- `APP_ENV=production`
- 强密码形式的 `POSTGRES_PASSWORD`、`MINIO_ROOT_PASSWORD`、`JWT_SECRET` 和 `ADMIN_AUDIT_PASSWORD`
- 生产环境的 `CORS_ALLOWED_ORIGINS`
- `GENERATION_PROVIDER=codex` 或 `codex_bridge`
- `ALLOW_FIXTURE_GENERATION=false`

通过 SSH 部署当前 Git 提交：

```powershell
.\scripts\deploy-ssh.ps1 `
  -KeyPath "$env:USERPROFILE\.ssh\id_ed25519" `
  -SshTarget "deploy@example.com" `
  -RemoteAppDir "/opt/kigcraft"
```

脚本会上传当前提交的 `git archive`，在服务器上解压，检查生产环境没有使用 fixture 生成，然后用 Docker Compose 重建 `api`、`worker` 和 `frontend`。

## 开发

前端：

```powershell
cd frontend
npm install
npm run dev
npm run build
```

后端：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\python -m pytest
```
