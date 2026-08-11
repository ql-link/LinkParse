# LinkParse

纯 CPU 文档解析服务。PDF 统一使用 OpenDataLoader + 按页 RapidOCR 管线；DOCX 使用
Mammoth 转语义 HTML，并收敛为 Markdown 产物。

## 能力

- `POST /v1/parse`：同步解析图片、PDF 或 DOCX
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
- PDF/图片按需输出 `text`、`json`、`markdown`、`html`；DOCX 固定输出 `markdown`
- 可选导出 PDF/DOCX 内嵌图片、OCR 页面图或原始图片到阿里云 OSS，并在 `assets` 返回 URL
- 独立用户 API Key、解析记录归属、魔数文件检测、大小/页数/DPI 限制和统一错误码
- Redis 分布式并发控制，分别限制 RapidOCR、OpenDataLoader 与 Word 的执行数量
- PDF 只有一条 `opendataloader_ocr` 管线，不暴露互斥引擎和强制 OCR 开关
- DOCX 只有一条 `mammoth_word` 管线；legacy `.doc` 暂不支持
- 文本 PDF 通常无需 OCR，扫描 PDF 通常逐页 OCR，混合 PDF 只 OCR 问题页
- OpenDataLoader 在独立子进程中运行，带解析超时、输出文件数/体积和日志体积保护
- Markdown 保留 `ODL_PAGE` 页码来源，并在 `meta.pdf.structure` 返回表格及页面溯源信息

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
# 离线 Docker 构建环境还需在目标 Python/平台上刷新 wheelhouse
python -m pip wheel --wheel-dir wheelhouse -r requirements-container.lock
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

OpenDataLoader 默认解析时限为 300 秒，最多生成 2000 个文件、512 MB 中间结果；可通过
`LINKPARSE_OPENDATALOADER_TIMEOUT_SECONDS`、`LINKPARSE_OPENDATALOADER_MAX_OUTPUT_FILES`
和 `LINKPARSE_OPENDATALOADER_MAX_OUTPUT_MB` 调整。表格策略默认为 `default`，可将
`LINKPARSE_OPENDATALOADER_TABLE_METHOD` 设为 `cluster`；复杂合并单元格需要保留 HTML 时，
设置 `LINKPARSE_OPENDATALOADER_MARKDOWN_WITH_HTML=true`。这些解析管线参数通过环境变量配置，
修改后需要重启 API 和 Worker。
异步任务遇到满载时不会失败，而是保持 `queued` 并自动重新排队。Redis 不可用时服务会
保守拒绝新的同步解析，并让异步任务稍后重试，避免退化为各进程独立计数后突破全局并发上限。

## 调用示例

```bash
curl -X POST http://localhost:8080/v1/parse \
  -H 'Authorization: Bearer change-me' \
  -F 'file=@resume.pdf' \
  -F 'output_formats=text,json,markdown,html' \
  -F 'include_images=true'
```

异步任务：

```bash
curl -X POST http://localhost:8080/v1/jobs \
  -H 'Authorization: Bearer change-me' \
  -F 'file=@scan.pdf'
```

## PDF 解析管线

| 输入 | 同一管线内的实际行为 |
|---|---|
| 图片 | RapidOCR |
| 全文本 PDF | OpenDataLoader 主结果通过质量门禁，OCR 页数通常为 0 |
| 扫描 PDF | OpenDataLoader 保留页面来源，问题页经 PyMuPDF 渲染后由 RapidOCR 补齐 |
| 混合 PDF | OpenDataLoader 保留整体结构，仅对质量门禁选中的页面执行 RapidOCR |

OpenDataLoader 的 Markdown 每页包含 `<!-- ODL_PAGE:n -->` 标记。响应的 `meta.pdf` 会给出
初始/最终质量报告、实际 OCR 页、解析器耗时/输出资源统计，以及 `structure.tables` 中的
Markdown/HTML 表格矩阵和来源页。OCR 文本以 `PAGE_FALLBACK:OCR` 标记追加在对应的
OpenDataLoader 页面内；OpenDataLoader 原文不会被覆盖。页码来源不完整、OCR 置信度不足或补齐后
仍未通过质量门禁时，解析会明确失败，不返回表面成功但内容不完整的结果。

## DOCX 解析管线

DOCX 上传后会先验证 OOXML 容器、成员路径、压缩比和解压后资源上限，再执行：

```text
OMML 公式转 LaTeX → Mammoth 语义 HTML → DOM 清理 → Markdown
```

标题、段落、粗体/斜体、链接、嵌套列表、表格和内嵌图片按文档顺序输出。简单表格转为
GFM Markdown 表格；合并单元格、多级表头、嵌套表格、图片或多段单元格等复杂表格转为
`table-rag-v1` 行级语义文本。多级表头使用 `/` 扁平化，纵向合并值在每个逻辑行中重复，
每行都使用“列名：值”表达，便于 RAG 按完整行切片后独立检索。表格内图片保留为 Markdown
图片链接，图片描述由下游 RAG 按需生成；嵌套表格拆成带父表引用的独立表格块。

简单和复杂表格都使用合法的 `LINKPARSE_TABLE_START/END` HTML 注释成对标记边界；注释只供
原始 Markdown 切片器识别，正文不依赖注释也能读取。解析器内部仍使用 TableIR 保存完整单元格
和合并关系，但 `table-rag-v1` 是面向检索的有损表示：模型可据此恢复扁平化后的逻辑行列，不能
无损恢复原 Word 的 `rowspan`、`colspan` 和视觉布局。只有 TableIR 构建失败时才保留安全 HTML
兜底。解析器按照 DOCX 中保存的
显式分页符和 `lastRenderedPageBreak` 顺序，从第 1 页开始编号；Markdown 使用
`<!-- WORD_PAGE:n -->` 标记页序。`meta.page_count` 返回推导页数，`meta.word` 同时返回分页来源、
章节数、公式数、表格数、图片数和解析告警。
Word 的自动排版页码受字体与环境影响，因此未保存分页标记的文档会按单页处理；Word 仍不提供 bbox。

DOCX 的最终产物固定为 `outputs.markdown`。请求中的 `output_formats` 只控制 PDF 和图片输出，
不会为 DOCX 额外生成 Text、JSON 或 HTML 产物。

## 数据保留

同步请求完成后立即删除原文件和中间图片。异步原文件在任务结束后删除，结果默认保留 24 小时。Worker 内置 Beat 调度器默认每 60 分钟执行一次清理：到期结果及其 OSS 图片会被删除并将任务标记为 `expired`，再经过一个结果 TTL 周期后删除任务元数据；同时清理同步请求的 OSS 图片清单、无主结果以及崩溃遗留的上传文件和临时目录。可通过 `LINKPARSE_CLEANUP_INTERVAL_MINUTES` 调整执行间隔，修改后需重启 Worker。

`include_images` 默认为 `false`。开启后，OpenDataLoader 会导出 PDF 内嵌图片，Mammoth 会导出
DOCX 内嵌图片，RapidOCR 解析 PDF 时会导出页面渲染图，直接上传图片时会保存原图。DOCX 图片
在上传 OSS 前会校验类型、单图大小和像素数，并按内容 SHA-256 去重。默认示例使用
`qingluo-public` 的 `LinkRarse/` 前缀和公共 OSS 域名，因此在结果保留期内返回不变的 URL；
对象到期后会被清理。若改为私有桶并清空 `LINKPARSE_OSS_PUBLIC_BASE_URL`，服务会返回有时效的签名 URL。

## 测试

```bash
pytest
ruff check .
```

解析引擎属于重依赖，单元测试不下载模型；部署后的验收应分别使用一张中英文图片、文本 PDF、
扫描 PDF、混合 PDF，以及包含标题、列表、复杂表格、公式和图片的 DOCX。

RapidOCR 适配器会优先使用 Python 包内自带的 ONNX 模型，从而避免首个线上请求临时下载模型。如果未来升级的 RapidOCR 包不再携带模型，应在构建镜像时准备模型，并通过适配器的模型配置指定本地路径。

## Jenkins CI/CD

`master` 分支推送后，由 Cloud Jenkins 唯一的 `linkparse` 生产 Job 打包当前提交并通过 Tailscale SSH 部署到 Primary。Primary 会先在 Docker `test` 阶段执行完整测试，再生成带提交与 Jenkins Build 编号的生产镜像。部署脚本会在切换服务前验证 MySQL 网络与连接，在发布后要求 `/health` 同时确认解析引擎和数据库可用，才判定发布成功。运行时复用主机共享 Redis 的独立 DB，不部署 LinkParse 专用 Redis 容器。

详细配置与服务器约定见 [`deploy/jenkins/README.md`](deploy/jenkins/README.md)。
