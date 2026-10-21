# 后端2：Agent 接入协议 v1

## 证据与边界

2026-10-21 基于上游 main `e3eeffafb0d90c380997fd1774c2758a09b4d199` 实现。

- [PR #1](https://github.com/52HzWhale111/agent/pull/1)：知识库资料，尚未合并。此 PR 不复制、不修改其中的教材资料。
- [PR #2](https://github.com/52HzWhale111/agent/pull/2)：压缩包内 `04_代码/agent_api.py` 定义 `/qa`、`/analyze_requirement`、`/review_document`、`/health`。三个业务处理器仍有 TODO 和示例返回。本接入层保留其路径和字段，不把示例返回当真实模型输出。
- 工程 Agent、业务后端、前端尚无已提交的可运行实现；下述工程协议是给队友实现的约定，未声称已经联调。
- 实现后端2职责，不代替 Agent 同学完成知识推理、编译沙箱或测试覆盖率算法。

## 本地启动

在仓库根目录：

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate；Linux: . .venv/bin/activate
python -m pip install -r gateway/requirements-dev.txt
python -m uvicorn gateway.main:app --host 127.0.0.1 --port 8000 --workers 1
python -m pytest gateway/tests -q
```

运行配置从进程环境读取；本地 uvicorn 不会自动读取 `.env`。可在 PowerShell 用 `$env:KNOWLEDGE_AGENT_URL='http://127.0.0.1:8100'` 设置。Compose 会读取仓库 `.env`。

本地 OpenAPI：`http://127.0.0.1:8000/docs`。配置 GATEWAY_API_KEY 后，使用带 Authorization 头的 OpenAPI 客户端或 curl；浏览器 Swagger 页的后续请求也需要认证。

默认知识 Agent 地址 `http://localhost:8100`、工程 Agent 地址 `http://localhost:8200`。PR #2 脚本默认 8001，独立服务启动时显式改端口：`uvicorn agent_api:app --host 0.0.0.0 --port 8100`。

## 前端调用

| 接入层接口 | 输入 | 上游调用 | 成功返回 |
|---|---|---|---|
| POST /api/qa | question，user_id 可选 | 知识 /qa | 200，原样返回上游 JSON 对象 |
| POST /api/requirements/analyze | description | 知识 /analyze_requirement | 200，上游 JSON 对象 |
| POST /api/uploads | multipart 字段 file | 本地安全存储 | 201，file_id、size、extension |
| POST /api/reviews | doc_type + content 或 file_id 二选一 | 排队后调用知识 /review_document | 202，task_id、status、progress、poll_after_seconds |
| POST /api/code-tests | code 或 ZIP 的 file_id 二选一；language=java；tests 可选 | 排队后调用工程 /run_tests | 202，任务号 |
| POST /api/consistency | code、diagram 对象 | 工程 /check_consistency | 200，上游 JSON 对象 |
| GET /api/tasks/{task_id} | 任务号 | 本地持久任务表 | status、progress、result、error、request_id |
| GET /health/live | 无 | 不检查 Agent | 接入层存活时 200 |
| GET /health/ready | 认证头 | 并行检查两个 Agent /health | 全部正常 200，否则 503 + services 明细 |

问答和需求拆解最多30秒，评审任务执行最多60秒，测试任务执行最多120秒。HTTP 上游调用限制总耗时，不会因持续少量返回数据而无限延长。队列等待时间单独计算，最多100个待处理/运行任务，可用 MAX_PENDING_TASKS 配置。客户端提交后每2秒轮询一次，终态即停止；提交任务不能用每2秒重复 POST 代替查询。

任务状态：`queued → running → succeeded / failed`。进度是阶段值0/10/100，**不是**真实编译或模型生成百分比；失败时保留停止阶段并展示 error。result 在成功时包含上游完整 JSON；失败时 error 含 code/message。

重启时，排队任务继续处理，已在运行的任务标记 WORKER_RESTARTED，提示重新提交，避免自动重复执行学生代码。已完成任务可继续查询。单进程 worker 与 API 在同一服务生命周期运行，**只能使用 --workers 1，不能多副本共享此队列**。现阶段课程单机负载使用一个串行队列；长任务较多时再拆 worker。

## 上游接口

知识 Agent 沿用 PR #2：

```json
POST /qa
{"question":"高内聚低耦合怎么判断？","user_id":"optional"}
响应: {"answer":"真实回答", "sources":["真实出处"], "retrieved_count":3}

POST /analyze_requirement
{"description":"校园失物招领系统"}
响应: {"actors":[],"use_cases":[],"main_flow":[],"exception_flow":[],"notes":"..."}

POST /review_document
{"content":"全文", "doc_type":"srs"}
响应: {"overall_comment":"...","issues":[{"location":"第3章","type":"empty_words","description":"...","suggestion":"..."}],"issue_count":{}}
```

工程 Agent 待队友确认并实现：

```json
POST /run_tests
{"code":"class Main {}", "language":"java", "tests":"..."}
或 {"archive_path":"/data/uploads/<随机串>.zip", "language":"java", "tests":"..."}
建议响应: {"compiled":true,"passed":3,"failed":1,"coverage":60,"uncovered_branches":[],"diagnostics":[],"failures":[]}

POST /check_consistency
{"code":"...","diagram":{"nodes":[],"edges":[]}}
建议响应: {"issues":[{"node_id":"...","reason":"...","suggestion":"..."}]}
```

两个 Agent 都需要 `GET /health`。返回2xx表示服务可用；如模型未配置或依赖未就绪，应返回503，不能用始终200掩盖不可用状态。上游响应暂时按 JSON 对象透传，具体结果字段由 Agent/前端共同确认。

网关会把同一个 `X-Request-ID` 传给上游；上游也应记录它。工程服务与网关共享 `/data` 卷，工程服务只读。ZIP 不在网关解压；工程服务必须验证路径穿越、符号链接、解压总大小，并由其真正的隔离容器执行代码，禁止在 API 容器内直接运行上传代码。这里没有实现或声称验证了沙箱本体。

## 文件、认证和错误

仅 doc/docx/pdf/zip；20 MiB（20×1024×1024字节）为硬上限，可调小不可调大。存储目录为 GATEWAY_DATA_DIR/uploads，文件名为 UUID + 标准化后缀；不使用用户文件名。检查基本文件签名，docx 必须含 word/document.xml。文件类型校验不等同恶意文件扫描；ZIP 只存储不执行。

评审接受 docx/pdf 并在任务内提取正文，docx 包括表格；扫描版 PDF 无 OCR 时返回 DOCUMENT_PARSE_FAILED。doc 可上传但需先转 docx/pdf 或提交 content，避免默默乱码；ZIP 用于代码任务。解析与 Agent 调用共用60秒任务预算。后台线程解析超时后 Python 无法强制停止线程，极复杂 PDF 仍可能继续占用线程；单机受信课程材料适用，公开大规模不可信上传应改为受资源限制的解析进程。

GATEWAY_API_KEY 为空时仅适合本地开发；Compose 强制填写，使用 `Authorization: Bearer <key>`。这是团队联调访问凭证，**不是学生登录系统**。不可把部署密钥硬编码进静态前端；演示可让测试者手动输入临时凭证，正式用户登录后应由业务后端在服务端代理注入，或后续接入统一用户认证。当前 file_id/task_id 是随机能力标识，不提供按学生账号的资源隔离，不能声称多用户权限验收已完成。

统一错误：`{"error":{"code":"AGENT_TIMEOUT","message":"这次有点慢，请重试"},"request_id":"..."}`。

| HTTP | code | 含义 |
|---|---|---|
| 401 | UNAUTHORIZED | 缺少/错误凭证 |
| 404 | FILE_NOT_FOUND / TASK_NOT_FOUND | 资源不存在 |
| 413 | FILE_TOO_LARGE | 超过文件限制 |
| 415 | FILE_TYPE_NOT_ALLOWED / INVALID_FILE_CONTENT | 类型不支持/内容不匹配 |
| 422 | INVALID_INPUT / TEXT_REQUIRED / DOCUMENT_PARSE_FAILED | 参数错误/需转换/正文解析失败 |
| 502 | AGENT_REJECTED / INVALID_AGENT_RESPONSE | 上游拒绝请求/返回格式异常 |
| 503 | AGENT_UNAVAILABLE / QUEUE_FULL | 该功能暂不可用/队列已满 |
| 504 | AGENT_TIMEOUT | 这次有点慢，请重试 |

任务失败码另有 WORKER_RESTARTED、TASK_FAILED。日志不记录请求正文、代码、文档内容或认证密钥。

## 实现参考

- [FastAPI 文件上传](https://fastapi.tiangolo.com/tutorial/request-files/)
- [Compose 启动依赖与健康检查](https://docs.docker.com/compose/how-tos/startup-order/)
- [Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)
