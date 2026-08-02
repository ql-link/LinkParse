# Jenkins CI/CD

LinkParse 使用与 LinkCV Development 相同的两机部署拓扑：

1. Cloud Jenkins checkout `ql-link/LinkParse` 的 `master` 分支。
2. Jenkins 将当前 Git 提交打包，通过 `/var/jenkins_home/.ssh/primary_dev` 发送到 Primary。
3. Primary 在临时目录构建 `test` 阶段并运行 `pytest`。
4. 测试通过后构建 `linkparse:<commit>-b<build>`，更新 `/opt/tolink/linkparse` 的 Compose 服务。
5. API 重建后刷新 Nginx，避免代理继续使用旧容器地址。
6. `/health` 确认解析引擎与 MySQL 均可用，且注册路由已生效后流水线才结束。

Primary 已启用 `linkparse-after-tailscale.service`。主机重启后，该单元会等待
`tailscale-online.target`，确认 `tailscale0` 已获得 `100.86.10.52`，再恢复
API/Worker、重建无状态 Nginx 端口绑定，并等待 `/health` 成功。
失败时每 15 秒自动重试。

## Jenkins Job

- Job：`linkparse`
- 仓库：`git@github.com:ql-link/LinkParse.git`
- 分支：`*/master`
- Script Path：`deploy/jenkins/Jenkinsfile`
- Git credential：`git-cred`
- Primary SSH key：`/var/jenkins_home/.ssh/primary_dev`

GitHub push webhook 使用现有 HTTPS 入口 `/jenkins-github-webhook`。令牌由 Jenkins Secret Text credential 管理，不写入仓库。

## Primary contract

- 部署目录：`/opt/tolink/linkparse`
- 运行配置：`/opt/tolink/linkparse/.env`，权限必须为 `600`
- 服务地址：`http://100.86.10.52:18743`
- 数据目录继续由服务器 `.env` 指向现有独立磁盘
- Redis 复用主机 `link_tolink-net` 中的 `tolink-redis`，LinkParse 使用独立 DB 5，不再部署 Redis 容器
- MySQL 由 `LINKPARSE_DATABASE_URL` 指向现有实例，并通过 `LINKPARSE_DATABASE_NETWORK` 接入对应 Docker 网络；二者是发布必填项
- 部署元数据：`/opt/tolink/linkparse/.deployment`

这是唯一的 LinkParse 生产流水线，不区分 Development/Production Job。流水线不会启动或修改 bge-m3，也不会清理其他项目镜像。
