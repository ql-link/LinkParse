# LinkParse

纯 CPU 文档解析服务。文本型 PDF 使用 OpenDataLoader，图片和扫描型 PDF 使用
RapidOCR + ONNXRuntime，PyMuPDF 负责 PDF 检测与页面渲染。

## 能力

- `POST /v1/parse`：同步解析图片或 PDF
- `POST /v1/jobs`：创建异步解析任务
- `GET /v1/jobs/{job_id}`：查询任务状态
- `GET /v1/jobs/{job_id}/result`：获取任务结果
- `GET /health`：检查服务和解析引擎
- `GET /`：内置可视化控制台
- `GET /v1/info`：服务能力和公开限制信息
- `GET/PUT/DELETE /v1/admin/config`：管理员账户的运行配置管理
- `POST /v1/auth/register`、`POST /v1/auth/login`：用户注册与登录
- `GET/POST/DELETE /v1/account/keys`：申请、查看与撤销用户 API Key
- `GET /v1/account/records`：查看当前用户的解析记录
- 输出 `text`、`json`、`markdown`、`html`
- 可选导出 PDF 内嵌图片、OCR 页面图或原始图片到阿里云 OSS，并在 `assets` 返回 URL
- 独立用户 API Key、解析记录归属、魔数文件检测、大小/页数/DPI 限制和统一错误码
- Redis 分布式并发控制，分别限制 RapidOCR 与 OpenDataLoader 的执行数量
- OpenDataLoader 失败时，`engine=auto` 自动降级到 RapidOCR

## 本地运行

前置条件：Python 3.10/3.11、Java 11+、Redis、MySQL 8.0+。

```bash
cp .env.example .env
# 配置 LINKPARSE_DATABASE_URL；首次启动会创建账户与记录表
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload
```

仓库同时提交 `uv.lock` 和由其导出的 `requirements-container.lock`。Docker 构建严格使用
`requirements-container.lock`，更新依赖时执行：

```bash
uv lock
uv export --frozen --no-dev --no-emit-project --no-hashes --output-file requirements-container.lock
```

异步 worker：

```bash
celery -A app.workers.celery_app:celery_app worker --loglevel=info --concurrency=1
```

## Docker Compose

```bash
cp .env.example .env
# 务必修改 LINKPARSE_API_KEYS
# 配置 LINKPARSE_DATABASE_URL，并确保 LINKPARSE_DATABASE_NETWORK 可访问目标 MySQL
# 使用图片输出时还需填写 OSS AccessKey Secret 等 LINKPARSE_OSS_* 配置
docker compose up --build -d
curl http://localhost:8080/health
```

浏览器打开 `http://localhost:8080/` 即可使用控制台。控制台包含：

- 服务与解析引擎健康状态
- 在线上传并解析 PDF/图片
- 注册、登录、申请独立 API Key 与撤销 Key
- 当前用户的同步/异步解析记录
- 基于用户 `is_admin` 权限的运行参数管理（普通账户不展示入口）
- 同步、异步 API 调用教程和路由规则

控制台登录会话和解析用 API Key 仅保存在当前标签页的 `sessionStorage`，不会跨标签页持久化。
密码使用带随机盐的 scrypt 摘要，登录会话与 API Key 在数据库中只保存 SHA-256 摘要；完整
API Key 仅在申请成功时展示一次。用户表 `users.is_admin` 决定账户能否管理运行策略；环境变量
`LINKPARSE_API_KEYS` 仅保留为自动化管理和旧调用方兼容通道。
运行配置写入 `data/config/runtime.json`，不包含 API Key、Redis 地址或数据目录。

空数据库中的新账户默认 `is_admin = false`。首次创建管理员账户后，由数据库管理员执行一次：

```sql
UPDATE users SET is_admin = TRUE WHERE username = 'root';
```

权限变更会在该账户下一次请求时生效，无需重新生成会话或 API Key。
部署环境也可以设置 `LINKPARSE_BOOTSTRAP_ADMIN_USERNAMES=root`。白名单账户首次注册或下一次
成功登录时会被持久化为管理员；移除环境变量不会自动撤销已经写入数据库的权限。
解析限制会对新请求即时生效；Celery 的硬超时设置需要重启 Worker 后完全生效。

生产环境应在 Nginx 前配置 TLS，或由云负载均衡终止 HTTPS。默认只绑定
`127.0.0.1:8080`；可通过 `LINKPARSE_BIND_ADDRESS` 和 `LINKPARSE_PORT` 调整。

默认 Celery Worker 提供 6 个总执行进程，实际吞吐由运行配置进一步限制：RapidOCR
默认为 1，OpenDataLoader 默认为 3。同步接口与异步 Worker 共用 Redis 槽位，因此不会
各自重复占用额度。可在控制台“运行配置”中调整；`LINKPARSE_WORKER_CONCURRENCY` 是部署
时的总容量上限，修改它需要重启 Worker。

`LINKPARSE_CONCURRENCY_WAIT_SECONDS` 控制同步请求等待槽位的时间，超时返回 HTTP 429。
异步任务遇到满载时不会失败，而是保持 `queued` 并自动重新排队。Redis 不可用时服务会
保守拒绝新的同步解析，并让异步任务稍后重试，避免退化为各进程独立计数后突破全局并发上限。

## 调用示例

```bash
curl -X POST http://localhost:8080/v1/parse \
  -H 'Authorization: Bearer change-me' \
  -F 'file=@resume.pdf' \
  -F 'engine=auto' \
  -F 'output_formats=text,json,markdown,html' \
  -F 'ocr=auto' \
  -F 'include_images=true' \
  -F 'dpi=200'
```

异步任务：

```bash
curl -X POST http://localhost:8080/v1/jobs \
  -H 'Authorization: Bearer change-me' \
  -F 'file=@scan.pdf'
```

## 路由规则

| 输入 | 默认引擎 |
|---|---|
| 图片 | RapidOCR |
| 全文本 PDF | OpenDataLoader |
| 扫描 PDF | PyMuPDF 渲染 + RapidOCR |
| 混合 PDF | OpenDataLoader；失败时 RapidOCR 兜底 |

`ocr=always` 强制 OCR，`ocr=never` 禁止自动 OCR。显式指定引擎时，如果该引擎失败不会静默降级。

## 数据保留

同步请求完成后立即删除原文件和中间图片。异步原文件在任务结束后删除，结果默认保留 24 小时。Worker 内置 Beat 调度器默认每 60 分钟执行一次清理：到期结果及其 OSS 图片会被删除并将任务标记为 `expired`，再经过一个结果 TTL 周期后删除任务元数据；同时清理同步请求的 OSS 图片清单、无主结果以及崩溃遗留的上传文件和临时目录。可通过 `LINKPARSE_CLEANUP_INTERVAL_MINUTES` 调整执行间隔，修改后需重启 Worker。

`include_images` 默认为 `false`。开启后，OpenDataLoader 会导出 PDF 内嵌图片；RapidOCR 解析 PDF 时会导出页面渲染图；直接上传图片时会保存原图。默认示例使用 `qingluo-public` 的 `LinkRarse/` 前缀和公共 OSS 域名，因此在结果保留期内返回不变的 URL；对象到期后会被清理。若改为私有桶并清空 `LINKPARSE_OSS_PUBLIC_BASE_URL`，服务会返回有时效的签名 URL。

## 测试

```bash
pytest
ruff check .
```

解析引擎属于重依赖，单元测试不下载模型；部署后的验收应分别使用一张中英文图片、文本 PDF、扫描 PDF 和混合 PDF。

RapidOCR 适配器会优先使用 Python 包内自带的 ONNX 模型，从而避免首个线上请求临时下载模型。如果未来升级的 RapidOCR 包不再携带模型，应在构建镜像时准备模型，并通过适配器的模型配置指定本地路径。

## Jenkins CI/CD

`master` 分支推送后，由 Cloud Jenkins 唯一的 `linkparse` 生产 Job 打包当前提交并通过 Tailscale SSH 部署到 Primary。Primary 会先在 Docker `test` 阶段执行完整测试，再生成带提交与 Jenkins Build 编号的生产镜像。部署脚本会在切换服务前验证 MySQL 网络与连接，在发布后要求 `/health` 同时确认解析引擎和数据库可用，才判定发布成功。运行时复用主机共享 Redis 的独立 DB，不部署 LinkParse 专用 Redis 容器。

详细配置与服务器约定见 [`deploy/jenkins/README.md`](deploy/jenkins/README.md)。
