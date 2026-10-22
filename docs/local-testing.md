> 更新：业务后端及工程服务已有新交付，当前认证/文件/工程协议请优先阅读 [1021-后端2联调-v2](1021-后端2联调-v2.md)。下文保留早期独立接入记录。

# 后端2本地运行与测试手册

## 现在运行的是什么

接入层是真实代码，两个 Agent 使用本地测试替身（Mock）：可以验证 HTTP 转发、任务排队、超时、文件存储、故障隔离。Mock 返回均带 `mock: true` 与说明，不调用模型、不评审正文、不编译代码、不计算覆盖率。

| 地址 | 用途 |
|---|---|
| http://127.0.0.1:8000/docs | 接入层 Swagger，主要测试入口 |
| http://127.0.0.1:8000/health/live | 接入层存活 |
| http://127.0.0.1:8000/health/ready | 两个 Agent 连通状态；正常200，故障503 |
| http://127.0.0.1:8100/docs | Mock 知识 Agent；可设置故障/延迟 |
| http://127.0.0.1:8200/docs | Mock 工程 Agent；可设置故障/延迟 |

没有前端同学的7个产品页面，当前通过 Swagger 测接口。服务仅绑定本机127.0.0.1；本地脚本关闭网关认证方便手动测试，不用于公网部署。

## 启动、停止与重启

打开 PowerShell，执行：

```powershell
cd 'D:\软工\agent'
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\start-local.ps1 -MockAgents
```

这会在后台启动三个进程，不弹额外窗口。启动前会检查端口；若服务已经启动，不需要重复执行。`-ExecutionPolicy Bypass` 只作用于这个进程，不修改系统执行策略。脚本使用已有 `.venv`，不会自动安装其他软件。

停止全部：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\stop-local.ps1
```

只停/启某一个服务（用于故障或重启测试）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\stop-local.ps1 -Service sandbox
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\start-local.ps1 -MockAgents -Service sandbox
```

`-Service gateway` 对网关单独操作，`-Service knowledge` 对知识替身单独操作。停止脚本读取本次启动的进程记录并核对命令行，只停止对应服务的进程树。中断运行任务会在重启后变为 WORKER_RESTARTED，排队和已完成结果继续保存。

## 点哪里、填什么、看什么

打开8000端口 `/docs`，展开目标接口，点 **Try it out**，粘贴请求体，点 **Execute**。返回内容看 **Server response** 下的状态码和 **Response body**。

### 1. 问答转发

`POST /api/qa`：

```json
{"question":"高内聚低耦合怎么判断？","user_id":"local-student"}
```

预期200，answer带【Mock】，mock为true，返回request_id。说明前端到网关到知识服务的调用通了，不代表回答质量已通过。

`POST /api/requirements/analyze` 可填：

```json
{"description":"做一个校园失物招领系统"}
```

预期200及mock=true；真实角色/用例需队友的知识Agent输出。

### 2. 长任务与轮询

`POST /api/reviews`：

```json
{"content":"1.项目背景：校园失物招领。2.功能：发布和认领。3.非功能：性能良好。","doc_type":"srs"}
```

删掉请求体中自动生成的 `file_id` 示例，只提供 content。预期立即202、status=queued、task_id、poll_after_seconds=2。复制 task_id，打开 `GET /api/tasks/{task_id}`，填入并 Execute，每2秒查询一次。

预期 queued → running → succeeded；Mock默认等2秒，手动查看时可能已经成功。终态 progress=100，result.mock=true、received_characters为实际传到上游的正文长度。0/10/100是排队/运行/完成阶段值，不是真实AI处理百分比。

`POST /api/code-tests`：

```json
{"code":"public class Main { public static void main(String[] args) {} }","language":"java","tests":""}
```

同样删掉 file_id 示例。预期202与任务号，查询后 succeeded，result.mock=true，compiled/coverage等为null——Mock故意不伪造编译和覆盖率。

### 3. 上传与接续任务

`POST /api/uploads` → Try it out → Choose File，选择真实 docx/pdf/zip/doc。可用你提供的 `具体工作说明.docx` 测上传；这只是测试文件，不会当作软件需求指令执行。

预期201，file_id为随机串。同一个文件上传两次，file_id必须不同，不能覆盖。

将 docx/pdf 返回的file_id放入 `POST /api/reviews`：

```json
{"file_id":"替换为上传返回的file_id","doc_type":"srs"}
```

不要同时保留 content。预期202，最终result.received_characters大于0，证明提取并转发了正文。docx包含表格文字。扫描版PDF无可提取文字时任务失败并返回 DOCUMENT_PARSE_FAILED。旧doc可上传，但需要转docx/pdf或直接提交content才能评审。

ZIP上传后可提交：

```json
{"file_id":"ZIP返回的file_id","language":"java","tests":""}
```

到 `POST /api/code-tests`，最终result.archive_received=true。Mock不解压、不执行代码。

错误测试：上传png/txt/exe应415；把txt改名pdf仍应415；大于20MiB应413；不存在的file_id应404；content/code与file_id同时提交应422。精确20MiB边界已经包含在自动测试中，不必手工造大文件。

### 4. 故障隔离

在8200端口 `/docs` → `POST /__test__/behavior`：

```json
{"delay_seconds":2,"unavailable":true}
```

接着8000端口 `/health/ready` 应503且sandbox=unavailable；提交代码任务后查询，应failed和AGENT_UNAVAILABLE（该功能暂不可用）；此时 `/api/qa` 仍200。最后把 unavailable 改回false，恢复正常。

也可用上面的 stop-local.ps1 -Service sandbox **真的关闭替身进程**，看同样的故障表现，然后用 start-local.ps1 -MockAgents -Service sandbox 恢复。两种做法都只验证网关对故障的处理，实际工程服务容器停服需上线后再验收。

### 5. 真实超时

在8100端口 `/__test__/behavior` 填：

```json
{"delay_seconds":35,"unavailable":false}
```

8000端口 `/api/qa` 发问，约30秒后应504与AGENT_TIMEOUT，中文提示“这次有点慢，请重试”。完成后把delay_seconds恢复2。

测试60秒评审超时：把8100替身delay_seconds设65，提交评审，按任务号轮询；执行约60秒后failed/AGENT_TIMEOUT。测试120秒代码超时：把8200替身delay_seconds设125，提交代码任务，执行约120秒后failed/AGENT_TIMEOUT。测试结束都恢复2。排队等待不计入任务执行超时，前一个慢任务会影响后一个任务何时开始。

### 6. 持久化和日志

记录一个成功任务号，停止并重新启动gateway，继续查原任务号，应仍succeeded。测试中断任务：把替身延迟调到15，提交评审并查询到running，停止gateway再重启，该任务应failed/WORKER_RESTARTED。完成后恢复Mock延迟2。

运行数据在 `D:\软工\agent\data\local`：

- uploads：随机文件名；
- gateway.sqlite3：上传索引和任务记录；
- logs/gateway.log：包含request_id、task_id、step、duration_ms；
- 三个服务的stdout/stderr.log：启动和运行错误。

用查询结果中的request_id搜索日志：

```powershell
Select-String -LiteralPath .\data\local\logs\gateway.log -SimpleMatch '替换为request_id'
```

重启不会删除测试数据，当前没有自动清理学生文件的逻辑。data目录已被git忽略。

## 自动验证（建议先跑这个）

```powershell
cd 'D:\软工\agent'
# 不需要运行服务，验证21项单元/契约/运维测试
& .\.venv\Scripts\python.exe -m pytest gateway/tests -q
# 需要三个本地服务，真实HTTP调用；默认约20秒
& .\.venv\Scripts\python.exe -m gateway.smoke_local
# 额外验证真实30秒超时，总耗时约50秒
& .\.venv\Scripts\python.exe -m gateway.smoke_local --timeout-check
```

smoke_local确认目标返回mock=true后才会修改其测试状态；结束会恢复健康和2秒延迟。运行时不要同时手动调整替身状态。自动测试成功也不等同真实Agent领域效果通过。

## 哪些现在能验，哪些等队友

| 项目 | 现在可验证 | 还缺什么/谁负责 |
|---|---|---|
| 总机转发、30/60/120秒超时 | 可，用Mock/自动测试 | Agent真实接口接入后再联调 |
| 任务队列、轮询、重启持久化 | 可，是真实SQLite队列 | 前端展示进度、错误和重试由前端1接入 |
| 20MiB上传、随机名、正文提取 | 可 | 文档列表/用户归属等业务持久化与权限需后端1协作 |
| 请求编号、耗时日志、健康检查 | 可 | 两个Agent内部沿用request_id，才能追到模型/检索/编译步骤 |
| 真实概念问答/需求拆解/文档评审 | 不可验证领域效果 | Agent知识完成PR #2的TODO、模型配置和真实出处；知识工程资料需合并/读取 |
| 真编译/JUnit/JaCoCo/死循环隔离 | 不可 | Agent工程提供真实隔离执行和/run_tests；Mock不会执行任何学生代码 |
| UML与代码实际一致性 | 仅可测接口转发 | 前端2确定diagram格式，Agent工程实现/check_consistency |
| 登录、学情、历史、数据库 | 当前网关不实现 | 后端1接口与数据，前端页面；共享访问凭证不能代替学生账号隔离 |
| Docker Compose/HTTPS/备份 | CI构建和配置校验通过；当前没有Docker运行环境 | 你准备服务器/域名、各同学提供镜像后部署并实际演练备份恢复 |
| 服务故障通知与监控截图 | 通知程序自动测试可验 | 真实通知渠道、服务依赖健康检查和线上故障演练；本地未启动生产monitor |

队友提供服务后，停止本地Mock，把真实服务分别放在8100/8200，或设置KNOWLEDGE_AGENT_URL/SANDBOX_AGENT_URL后只启动gateway。`start-local.ps1`不加-MockAgents会保留进程环境中的两个地址；不自动读取.env。你负责把这些接口联通并完成部署，队友负责让Agent输出真实结果。

本地启动和测试脚本不执行Git操作。代码提交和PR状态以当前交付记录为准。

## 不用队友即可继续做的运维验证

网关备份、校验和恢复命令见deploy.md“网关：无需队友即可做本地备份恢复”。本轮已对真实本地数据演练，原数据未覆盖；本地证据文件为 `data/local/backup-drill.json`。恢复只允许新目录，不能覆盖在线数据。

监控支持本地地址和单次检查，PowerShell示例（这不会安装定时任务或发送外部消息）：

```powershell
$env:MONITOR_TARGETS = '{"gateway":"http://127.0.0.1:8000/health/ready","knowledge":"http://127.0.0.1:8100/health","sandbox":"http://127.0.0.1:8200/health"}'
$env:ALERT_WEBHOOK_URL = ''
& .\.venv\Scripts\python.exe -m infra.monitor --once
Remove-Item Env:MONITOR_TARGETS
Remove-Item Env:ALERT_WEBHOOK_URL
```

全部正常退出码0，有故障退出码1；输出services内的true/false。去掉--once为持续检查，Ctrl+C停止。外部通知仍需填写真实接收地址，通用JSON格式与接收平台是否匹配需要确认。本轮已用本地HTTP接收端验证故障及恢复各通知一次，证据在 `data/local/monitor-drill.json`；不等同外部通知平台配置完毕。

带访问密钥时，Swagger文档仍可打开，点击Authorize填入密钥后再测试业务接口；当前快捷启动脚本只用于本地无认证联调。
