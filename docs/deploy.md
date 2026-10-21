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
8. `GET /health/ready` 携带网关访问凭证，确认知识/工程服务真实可用。监控逐项检查网关、前端、后端和两个 Agent，每轮间隔30秒（单个检查最多5秒）；故障及恢复仅在状态变化时发通知，失败投递会重试。服务内置健康端点应检查必要依赖；业务 /health 应包含数据库连通性。

Caddy 持久化证书并自动续期；所有服务设置 unless-stopped，Docker 自启动后容器随之恢复。故意用 docker compose stop 停止的服务应手动 start，不能把管理员主动停止误认为重启策略失败。网关 liveness 不依赖 Agent，单个 Agent 故障不会把其他 API 一起停掉。数据库仅内网访问，网关8000只绑定服务器回环地址。

## 每日备份与恢复

先手动运行 `sh infra/backup.sh`。成功后生成 `backups/snapshot-<UTC时间>-<进程号>/`，含 `postgres.sql.gz` 与 `gateway/`（SQLite快照、上传文件、SHA-256清单）。整套流程完成前使用 `.partial-*` 目录；PostgreSQL导出、网关快照或校验失败时脚本返回非零，不能把.partial当成成功备份。业务DB与网关DB分别取快照，不构成跨库同一时刻的事务，恢复后需核对业务document_id与file_id等关联。

复制到宿主的网关快照会再次用只读挂载校验；成功后清理本次在容器内创建的暂存快照。失败时可能保留.partial或容器暂存目录，排查后由管理员确认再清理。备份暂不自动删除历史快照，课程期间请监测空间、保留至少最近7天并定期加密同步异机。同机磁盘损坏时同机备份无法恢复。

### Linux每日定时配置（准备好服务器后执行）

提供 `infra/zhigou-backup.service` 和 `.timer`，默认项目在 `/opt/agent`，定于北京时间03:15运行，错过的计划在开机后补执行。如项目路径不同，先修改service里的WorkingDirectory和ExecStart。

```sh
sudo install -m 644 infra/zhigou-backup.service infra/zhigou-backup.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/zhigou-backup.service /etc/systemd/system/zhigou-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now zhigou-backup.timer
sudo systemctl list-timers zhigou-backup.timer
# 首次手动试跑并核对结果
sudo systemctl start zhigou-backup.service
sudo journalctl -u zhigou-backup.service -n 50 --no-pager
```

也可保留原先每天03:15的cron方式，但两者只启用一个，避免重复备份。systemd配置参考 [systemd.timer](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html) 与 [systemd.time](https://www.freedesktop.org/software/systemd/man/latest/systemd.time.html)。当前Windows未安装或启用此timer，服务器时钟/时区数据库和实际触发需到Linux上验收。备份失败记录在journal，暂未接入外部备份失败通知；不能把服务故障Webhook当作备份结果告警。

### 网关：无需队友即可做本地备份恢复

Python标准库即可运行，不需要Docker，也不会调用Agent或修改源数据：

```powershell
cd 'D:\软工\agent'
& .\.venv\Scripts\python.exe -m infra.gateway_backup backup data/local backups/manual-demo-01
& .\.venv\Scripts\python.exe -m infra.gateway_backup verify backups/manual-demo-01
& .\.venv\Scripts\python.exe -m infra.gateway_backup restore backups/manual-demo-01 data/restored-demo-01
```

目标目录必须不存在；再次演练请换新名字。恢复前校验SQLite完整性、注册文件清单、大小与SHA-256；损坏、缺文件、路径穿越均拒绝恢复。备份只复制数据库快照引用的上传文件，避免把未完成上传或无关文件混进快照。源数据仍运行在data/local，恢复演练目录独立。恢复到新目录时会修正排队ZIP任务的archive_path；运行中的任务由网关启动时标记为WORKER_RESTARTED。

要使用恢复数据，停止旧网关，设置 `GATEWAY_DATA_DIR` 到新目录，用uvicorn直接启动；本地快捷启动脚本固定使用data/local，不能用它切换恢复目录。生产恢复也应先停止写入、使用新目录或新卷，保留旧卷作回退，确认UID10001可写，再让网关使用恢复的数据。不要覆盖在线SQLite或盲删-wal/-shm。

### PostgreSQL恢复（依赖真实业务数据库）

先在演练环境恢复到空数据库；备份未带--clean，不能直接覆盖已有同名表。以下为路径模板，不要将示例时间戳当实际文件名：

```sh
# gzip -dc backups/snapshot-<时间>-<进程号>/postgres.sql.gz > /tmp/zhigou-restore.sql
# docker compose --env-file .env -f infra/docker-compose.yml exec -T db psql -v ON_ERROR_STOP=1 -U zhigou -d zhigou < /tmp/zhigou-restore.sql
```

源PostgreSQL用户、库名当前为zhigou，需与后端1确认。真实业务表恢复、演示账号、文档关联记录仍需队友数据与服务器环境验收；本地SQLite恢复成功不能代替PostgreSQL整套恢复通过。

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
| 备份/恢复演练结果 | 网关本地演练已通过；PostgreSQL及服务器整套待验收 |
