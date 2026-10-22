# 后端2部署与交接

本轮真实业务接口、工程文本协议及本地验证见 [1021-后端2联调-v2.md](1021-后端2联调-v2.md)。

## 当前交付状态

接入层、持久任务队列、文件上传、Dockerfile、完整服务拓扑的 Compose、HTTPS 入口配置、备份/部署脚本、HTTP 故障通知程序及 CI 已提供。仓库原有两个 PR 的内容及边界见 agent-integration.md。

**后端已在香港 ECS 上试运行，前端仍待交付。** 公网网关文档为 https://zhigou.47-239-43-172.sslip.io/docs ，业务文档为 https://zhigou.47-239-43-172.sslip.io/business/docs 。真实数据库、Java/JUnit 沙箱与 DeepSeek 三链路已在服务器验收；试运行模型调用设有不自动重置的总限额。当前实际配置、验证记录、测试步骤和费用边界见 [香港试运行交付](1022-香港试运行-v1.md)。原本地联调记录继续保留，属于各自当时的状态。

## 服务器准备好后的步骤

建议 Linux 单机，安装并启用 Docker Engine 与 Compose 插件。开放80/443端口；域名 A/AAAA 指向服务器，未提供 IPv6 服务时不要保留错误 AAAA 记录。

1. 下载 Fork 的 feat/backend2-integration 分支；复制根目录 `.env.example` 为 `.env`。
2. 填写 DOMAIN、ACME_EMAIL；生成随机 GATEWAY_API_KEY、POSTGRES_PASSWORD 和至少32字符的 JWT_SECRET_KEY。数据库密码使用 URL 安全字符，例如64位十六进制。真实值只存服务器，不提交 Git。
3. 填写 FRONTEND_IMAGE；业务、知识和沙箱API已有Dockerfile可从代码构建，KNOWLEDGE_IMAGE、SANDBOX_IMAGE、BACKEND_IMAGE可选。各服务内部端口为3000、8000、8100、8200。sandbox-execution是仅供构建的profile，deploy.sh先构建执行镜像，再启动正常服务；不要手工用该profile执行up。建议发布时固定不可变tag/digest。
4. 知识服务填写 LLM_API_KEY、LLM_BASE_URL=https://api.deepseek.com、LLM_MODEL=deepseek-flash。SANDBOX_WORK_DIR填Docker宿主机绝对目录，宿主/API容器内保持同路径；首次使用空的专用目录，由Docker创建，或确认仅该目录归属UID 0可写。API已丢弃DAC覆盖能力，预建为其他用户所有时会拒绝写入；不要放宽整个仓库权限。API仅在私有容器网络监听8200，不发布端口。API挂载该专用主机的Docker socket以创建受限执行容器，**socket的只读挂载不会限制Docker API权限**，该API进程具备主机Docker管理能力。因此使用专用部署环境，仅允许受信网关访问；不向学生开放Docker socket、API端口或任意镜像/路径参数。执行容器仍无网络、256MiB、5秒测试限时及能力丢弃。
5. 填写 ALERT_WEBHOOK_URL：能接收 `{service,status,time}` JSON 的 HTTPS 通知入口；这里不是直接兼容所有飞书/钉钉/邮件接口，需要通知服务或格式转换。未填写仅打印日志，**不会收到通知**。
6. 运行 `sh infra/deploy.sh`，查看 `docker compose --env-file .env -f infra/docker-compose.yml ps`。
7. 确认公网 `https://<域名>/` 打开；接口 `/api/*` 到网关，其中 `/api/v1/*` 转发真实业务接口；`/business/*` 去掉此前缀后到业务后端。Agent接口使用业务登录的JWT，GATEWAY_API_KEY仅作为运维凭证。业务格式见 backend/README.md。
8. `GET /health/ready` 携带网关运维凭证，确认依赖路由可达。工程探测仅OPTIONS /sandbox/run-tests，不代表Docker执行已验证；上线仍须提交一次真实Java/JUnit任务。监控逐项检查网关、前端、后端和两个 Agent，每轮间隔30秒（单个检查最多5秒）；故障及恢复仅在状态变化时发通知，失败投递会重试。业务 /health 已检查数据库连通性。

Caddy 持久化证书并自动续期；所有服务设置 unless-stopped，Docker 自启动后容器随之恢复。故意用 docker compose stop 停止的服务应手动 start，不能把管理员主动停止误认为重启策略失败。网关 liveness 不依赖 Agent，单个 Agent 故障不会把其他 API 一起停掉。数据库仅内网访问，网关8000只绑定服务器回环地址。

## 每日备份与恢复

完整校验、云端模型限额计数、离线加密导出与隔离恢复工具已补充，操作与本轮验证见 [1022-备份加密与恢复-v1.md](1022-备份加密与恢复-v1.md)。此轮交付代码不代表已更新生产脚本或建立异机副本。

后续已完成生产脚本部署与一次生产密文的本机恢复；自动加密、受认证 HTTPS 拉取和 Windows 计划任务见 [自动异机备份 v1](1022-自动异机备份-v1.md)，包含本机关机、流量和空间限制。

先手动运行 `sh infra/backup.sh`。成功后生成 `backups/snapshot-<UTC时间>-<进程号>/`，含 `postgres.sql.gz`、`gateway/`（SQLite快照、任务文件、SHA-256清单）、`backend-uploads/`（业务原文档）及其SHA-256清单。整套流程完成前使用 `.partial-*` 目录；导出、复制或校验失败时脚本返回非零，不能把.partial当成成功备份。业务DB与网关DB分别取快照，不构成跨库同一时刻的事务，恢复后需核对业务document_id与file_id等关联。

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

源PostgreSQL用户、库名为zhigou。还需在停止写入的演练环境校验 backend-uploads.sha256，将业务原文档恢复到新的 backend-uploads 卷，确认业务容器用户可写，再同时接回恢复的数据库与文件卷。仅恢复网关任务副本不能恢复业务原文档。本地真实PostgreSQL 17与原文档配套恢复已演练通过，命令为 `.\.venv\Scripts\python.exe -m infra.smoke_postgres --wsl-distro Debian`。服务器实际卷、定时备份与异机副本仍需上线环境验收。

## 日志和故障排查

应用日志在 gateway-data 卷 `/data/logs/gateway.log`，按 UTC 天切分保留14份；包含 request_id、task_id、step、duration_ms。容器 stdout/Caddy 访问日志按10MB轮转保留5份。按 request_id 找到入口请求和上游步骤；待队友在 Agent 内部沿用该编号后才能追踪检索/模型/编译子步骤。备份/监控日志不包含访问凭证。

监控截图应来自真实部署：保留服务全正常、sandbox停止后的故障通知、恢复通知三张；不得以单元测试结果或示例截图代替。

## 10月24日答辩前待填

| 项目 | 实际值/状态 |
|---|---|
| 公网地址 | 香港后端 HTTPS 已上线，地址与当时验收见 1022-香港试运行-v1.md；前端待交付 |
| 测试账号 | 本地已有后端1演示账号；生产不自动创建，须单独初始化 |
| 知识真实模型调用 | DeepSeek三链路已通过；20案例已运行，资料/误报与人工验收待补 |
| 工程服务镜像 | 执行镜像与HTTP API Dockerfile已提供；本地8组真实执行通过 |
| 前端/业务后端镜像 | 前端待交付；业务后端已有Dockerfile与CI构建 |
| 告警接收渠道与截图 | 云端故障/恢复日志已演练，外部通知接收渠道仍未配置 |
| 备份/恢复演练结果 | 服务器每日同机备份已启用；本地全量加密与真实PostgreSQL恢复已补验，生产异机副本/恢复仍待配置 |
