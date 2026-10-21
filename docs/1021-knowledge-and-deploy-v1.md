# 后端2：知识服务与部署补充 v1

本轮保留PR v1版本，继续使用Fork分支 `feat/backend2-integration`。不覆盖知识同学的原压缩包或原工程Java/Dockerfile。

## 已完成与验证范围

| 交付 | 当前状态 | 复现入口 |
|---|---|---|
| 知识Agent | 解出原10个文件，补真实HTTP模型调用、检索、Prompt填充、JSON/出处校验及错误处理 | `agents/knowledge/README.md` |
| 知识模型 | DeepSeek-V4.1-Flash-1017官方别名 `deepseek-flash`；本机已配置并跑通3条真实模型链路 | `data/local/knowledge.env`（忽略，不提交） |
| 知识案例 | 20条真实接口调用完成；发现资料缺口、预期清单冲突及评审误报，未作语义通过承诺 | `docs/1021-knowledge-validation-v1.md` |
| 自动测试 | 69项通过，含新增6项知识协议测试；模型替身仅证明协议与异常处理 | `python -m pytest backend/tests gateway/tests -q` |
| PostgreSQL | 真实17.11数据库完成7项检查：初始化、权限/原文件、图版本、答题幂等、重启、备份校验、恢复业务数据一致 | `python -m infra.smoke_postgres --wsl-distro Debian` |
| 沙箱服务化 | 新增HTTP API Dockerfile，与执行镜像分开；Compose内私有访问、相同路径工作目录及受限执行容器 | `agents/sandbox/Dockerfile.server` |
| 容器验证 | 3组检查通过：API镜像内真实JUnit成功/死循环限时、知识未配置Key的明确失败；测试容器清理通过 | `python -m infra.smoke_containers --wsl-distro Debian` |
| 真实工程能力 | 之前8组网关→Java→Docker/JUnit场景通过，计数、行覆盖率、隔离与容器回收已有证据 | `docs/1021-sandbox-local-v1.md` |

本地演练使用唯一命名的临时数据库容器和卷，不修改正在运行的业务SQLite；测试完成清理本次Docker资源，SQL与文件快照保留在忽略目录。恢复至新的空库和新上传目录后，重新通过真实业务API检查登录、跨用户拒绝、DOCX字节内容、图v1/v2和学情数据。临时端口只绑定回环，重启保持同一端口。

## 本机知识服务接入

当前8000网关与8001真实业务继续沿用，8200为真实本地沙箱。8100运行已配置的真实知识服务。首次启动整套服务可按沙箱运行说明执行，最后替换知识模拟服务：

```powershell
cd 'D:\软工\agent'
# 先在 data/local/knowledge.env 填真实Key，Base URL和模型名已准备好。
powershell -NoProfile -ExecutionPolicy Bypass -File infra/stop-local.ps1 -Service knowledge
powershell -NoProfile -ExecutionPolicy Bypass -File infra/start-knowledge.ps1
& .\.venv\Scripts\python.exe -m gateway.smoke_knowledge
```

`smoke_knowledge`使用后端1本地演示账号经网关实际调用问答、需求拆解、DOCX上传→评审→轮询，结果记录到忽略目录 `data/local/knowledge-smoke.json`，不保存Token。它验证代表性链路，不代替20个案例的领域语义评测；不能把HTTP成功当作答案正确、检出率达标。

2026-10-21三条真实链路已通过。另以 `python -m agents.knowledge.evaluate_cases` 跑完原20案例，详细初查及知识/测试对接问题见 [真实知识验证记录](1021-knowledge-validation-v1.md)。

## 部署前可复现的检查

```powershell
& .\.venv\Scripts\python.exe -m pytest backend/tests gateway/tests -q
& .\.venv\Scripts\python.exe -m infra.smoke_postgres --wsl-distro Debian
# 先构建两个API镜像和工程执行镜像，再运行容器验证。
& .\.venv\Scripts\python.exe -m infra.smoke_containers --wsl-distro Debian
```

Linux或WSL中的构建命令（仓库根目录）：

```sh
docker build -f agents/knowledge/Dockerfile -t zhigou-knowledge:local .
docker build -f agents/sandbox/Dockerfile.server -t zhigou-sandbox-api:local .
docker build -t zhigou-sandbox-jdk17:v1 agents/sandbox
```

Windows挂载盘若出现`failed to xattr .pytest_cache`，将**干净Git工作副本**放WSL Linux文件系统再构建，避免把运行中的虚拟环境、缓存和数据一起带入构建上下文。本机曾遇到镜像仓库认证超时，按既有Windows代理下载官方镜像、逐层SHA256校验后导入；没有更改全局WSL网络、防火墙或代理监听。

本机WSL会在没有Windows前台客户端后自动退出，曾导致容器请求中途断连。`start-sandbox.ps1`为当前项目保存一个隐藏的WSL保活进程，停止沙箱时核对PID和命令后回收；两份Docker演练脚本只在自己的执行期间保活，finally中释放。没有修改全局WSL空闲时间或其他发行版。

生产Compose已能从源码构建知识与沙箱API；`deploy.sh`先单独构建sandbox-execution profile，普通up不运行该构建目标。沙箱API需要管理专用部署主机的Docker引擎，socket只读挂载仍具备管理权限，不能对公网发布该接口。具体环境变量、HTTPS、备份与监控步骤见 [部署说明](deploy.md)。

## 后续对接

| 责任方 | 仍需提供或执行 |
|---|---|
| 模型账号负责人 | 本机已配置并完成真实调用；上线时单独配置服务器凭据与额度 |
| 知识Agent与测试 | 已有20条实际响应，需按验证记录修资料缺口、统一DEF标准、复核出处与误报；补定制前后同输入对照与人工验收 |
| 前端1/前端2 | 可运行代码或镜像、构建说明、API地址配置；联调JWT、任务轮询、文档错误、UML版本冲突和结果展示 |
| Agent工程 | 图代码一致性接口、细粒度分支定位及约定范围内的项目样例；ZIP执行仍不支持，网关明确拒绝 |
| 组长/部署负责人 | 云服务器、登录方式、域名及通知接收渠道；审阅后合并当前PR |
| 后端2与测试 | 上线后验证HTTPS、服务故障/恢复通知、整机重启自启动、定时备份和服务器新卷恢复，补公网地址、账号与监控截图 |

本轮不代替前端交付、不创建付费账号、不宣称已上线。团队首次作业报告可引用已验证的接口、分层架构、真实沙箱与数据库证据；真实模型效果和公网访问必须在实际运行后补充。
