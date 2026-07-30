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
- `GET/PUT/DELETE /v1/admin/config`：鉴权后的运行配置管理
- 输出 `text`、`json`、`markdown`、`html`
- 可选导出 PDF 内嵌图片、OCR 页面图或原始图片到阿里云 OSS，并在 `assets` 返回 URL
- API Key、魔数文件检测、大小/页数/DPI 限制和统一错误码
- OpenDataLoader 失败时，`engine=auto` 自动降级到 RapidOCR

## 本地运行

前置条件：Python 3.10/3.11、Java 11+、Redis。

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload
```

仓库同时提交 `uv.lock` 和由其导出的 `requirements.lock`。Docker 构建严格使用
`requirements.lock`，更新依赖时执行：

```bash
uv lock
uv export --frozen --no-dev --no-emit-project --no-hashes --output-file requirements.lock
```

异步 worker：

```bash
celery -A app.workers.celery_app:celery_app worker --loglevel=info --concurrency=1
```

## Docker Compose

```bash
cp .env.example .env
# 务必修改 LINKPARSE_API_KEYS
# 使用图片输出时还需填写 OSS AccessKey Secret 等 LINKPARSE_OSS_* 配置
docker compose up --build -d
curl http://localhost:8080/health
```

浏览器打开 `http://localhost:8080/` 即可使用控制台。控制台包含：

- 服务与解析引擎健康状态
- 在线上传并解析 PDF/图片
- API Key 鉴权后的运行参数管理
- 同步、异步 API 调用教程和路由规则

控制台输入的 API Key 仅保存在当前标签页的 `sessionStorage`，不会由页面持久化。
运行配置写入 `data/config/runtime.json`，不包含 API Key、Redis 地址或数据目录。
解析限制会对新请求即时生效；Celery 的硬超时设置需要重启 Worker 后完全生效。

生产环境应在 Nginx 前配置 TLS，或由云负载均衡终止 HTTPS。默认只绑定
`127.0.0.1:8080`；可通过 `LINKPARSE_BIND_ADDRESS` 和 `LINKPARSE_PORT` 调整。

默认部署限制为一个同步解析进程和一个异步解析进程，两个进程各使用 3 个
ONNXRuntime intra-op 线程。这样最多同时执行两个重型解析任务，适合 6 核 CPU
且还承载其他服务的主机。

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

`main` 分支推送后，由 Cloud Jenkins 唯一的 `linkparse` 生产 Job 打包当前提交并通过 Tailscale SSH 部署到 Primary。Primary 会先在 Docker `test` 阶段执行完整测试，再生成带提交与 Jenkins Build 编号的生产镜像，完成健康检查后才判定发布成功。运行时复用主机共享 Redis 的独立 DB，不部署 LinkParse 专用 Redis 容器。

详细配置与服务器约定见 [`deploy/jenkins/README.md`](deploy/jenkins/README.md)。
