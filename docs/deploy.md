# 后端2部署与交接

## 当前交付状态

接入层、持久任务队列、文件上传、Dockerfile、完整服务拓扑的 Compose、HTTPS 入口配置、备份/部署脚本、HTTP 故障通知程序及 CI 已提供。仓库原有两个 PR 的内容及边界见 agent-integration.md。

**尚未上线。** 当前没有云服务器、域名、通知接收地址、真实团队服务镜像或模型密钥；不能提供公网网址、实际测试账号、线上监控截图。当前电脑没有 Docker，容器构建、Caddy/Compose 校验由 CI 执行，实际上线验收须在服务器执行。CI 中的镜像名称仅用于配置语法检查，不是可部署成品。

## 服务器准备好后的步骤

建议 Linux 单机，安装并启用 Docker Engine 与 Compose 插件。开放80/443端口；域名 A/AAAA 指向服务器，未提供 IPv6 服务时不要保留错误 AAAA 记录。

1. 下载本 PR 的代码；复制根目录 `.env.example` 为 `.env`。
2. 填写 DOMAIN、ACME_EMAIL；生成随机 GATEWAY_API_KEY 与 POSTGRES_PASSWORD。数据库密码使用 URL 安全字符，例如64位十六进制。真实值只存服务器，不提交 Git。
3. 填写 FRONTEND_IMAGE、BACKEND_IMAGE、KNOWLEDGE_IMAGE、SANDBOX_IMAGE，建议固定不可变 tag/digest。镜像必须真实实现 docs/agent-integration.md 的端口/路径：3000、8000、8100、8200。镜像需读取 PORT 或在启动命令中固定正确端口。不要将 PR #2 的 TODO 示例直接当上线知识服务。
4. 知识服务填写 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL。工程服务的沙箱隔离由 Agent 工程交付；本 Compose 不挂宿主 Docker socket，队友若依赖 Docker 执行器，需单独设计受控执行接口后联调，不能通过给公网 API 特权容器替代隔离。
5. 填写 ALERT_WEBHOOK_URL：能接收 `{service,status,time}` JSON 的 HTTPS 通知入口；这里不是直接兼容所有飞书/钉钉/邮件接口，需要通知服务或格式转换。未填写仅打印日志，**不会收到通知**。
6. 运行 `sh infra/deploy.sh`，查看 `docker compose --env-file .env -f infra/docker-compose.yml ps`。
7. 确认公网 `https://<域名>/` 打开；接口 `/api/*` 到网关，业务 `/business/*` 去掉此前缀后到业务后端。前端需按此前缀联调；业务接口具体字段由后端1提供。
8. `GET /health/ready` 携带网关访问凭证，确认知识/工程服务真实可用。监控每30秒检查网关、前端、后端和两个 Agent；故障及恢复仅在状态变化时发通知，失败投递会重试。服务内置健康端点应检查必要依赖；业务 /health 应包含数据库连通性。

Caddy 持久化证书并自动续期；所有服务设置 unless-stopped，Docker 自启动后容器随之恢复。故意用 docker compose stop 停止的服务应手动 start，不能把管理员主动停止误认为重启策略失败。网关 liveness 不依赖 Agent，单个 Agent 故障不会把其他 API 一起停掉。数据库仅内网访问，网关8000只绑定服务器回环地址。

## 每日备份

先手动运行 `sh infra/backup.sh`。成功后在服务器配置 cron（示例服务器时区设 Asia/Shanghai，每天03:15）：

```cron
15 3 * * * cd /opt/agent && sh infra/backup.sh >> /opt/agent/backups/cron.log 2>&1
```

先创建 backups 目录，再安装该条任务；定时服务启用状态需检查。脚本备份 PostgreSQL 的 pg_dump、网关 SQLite 在线快照及上传文件。失败返回非零，部分 PostgreSQL 文件用 .tmp 后缀，成功后改名；数据库保留7天，上传快照由管理员按课程要求手动清理。备份目录只对服务器管理员开放；定期加密同步异机，单机磁盘故障时同机备份无法恢复。监控 webhook 不会自动监视 cron 退出码，需在服务器另配 cron 邮件或运维告警。

PostgreSQL 恢复（先在演练环境验证；恢复会写入目标库）：

```sh
# 先停业务写入，选择经过确认的时间戳，不要把示例当实际文件名
# gzip -dc backups/postgres-<timestamp>.sql.gz > /tmp/zhigou-restore.sql
# docker compose --env-file .env -f infra/docker-compose.yml exec -T db psql -v ON_ERROR_STOP=1 -U zhigou -d zhigou < /tmp/zhigou-restore.sql
```

恢复到空数据库；该备份没有 --clean，不能直接覆盖已有同名表。SQLite 恢复时先停止网关，把目标 /data/gateway.sqlite3、-wal、-shm 移到保留目录，再复制快照与匹配 uploads 快照，确保UID10001可写，最后启动网关。详细路径用 `docker volume inspect` 确认，禁止盲删命名卷。恢复后验证已完成任务查询、上传文件可读取、业务表记录数量。尚未做服务器恢复演练，不能勾选验收完成。

## 日志和故障排查

应用日志在 gateway-data 卷 `/data/logs/gateway.log`，按 UTC 天切分保留14份；包含 request_id、task_id、step、duration_ms。容器 stdout/Caddy 访问日志按10MB轮转保留5份。按 request_id 找到入口请求和上游步骤；待队友在 Agent 内部沿用该编号后才能追踪检索/模型/编译子步骤。备份/监控日志不包含访问凭证。

监控截图应来自真实部署：保留服务全正常、sandbox停止后的故障通知、恢复通知三张；不得以单元测试结果或示例截图代替。

## 10月24日答辩前待填

| 项目 | 实际值/状态 |
|---|---|
| 公网地址 | 待服务器与域名 |
| 测试账号 | 待后端1创建；网关凭证不等同用户账号 |
| 知识真实模型调用 | PR #2 仍为骨架，待 Agent 知识完成 |
| 工程服务镜像 | 待 Agent 工程交付 |
| 前端/业务后端镜像 | 待对应同学交付 |
| 告警接收渠道与截图 | 待部署配置与故障演练 |
| 备份/恢复演练结果 | 待服务器验收 |
