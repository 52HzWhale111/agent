# 真实沙箱本地运行与验证

## 已验证的环境

2026-10-21 本地已跑通：真实后端1登录 → 8000网关持久任务 → 8200 Java HTTP服务 → WSL Docker容器 → JUnit/JaCoCo结果 → 按用户查询任务。

| 组件 | 本机版本 / 位置 |
|---|---|
| Java | Eclipse Temurin 17.0.20.1，`data/local/tools/java/` |
| Maven | 3.9.16，`data/local/tools/maven/` |
| WSL | 3.0.3，Debian 13，虚拟磁盘位于 `data/local/wsl-debian/` |
| Docker Engine | 官方签名APT仓库安装的29.9.2，运行在Debian内 |
| 执行镜像 | `zhigou-sandbox-jdk17:v1`，JDK17、JUnit Console 1.10.2、JaCoCo 0.8.12 |

开发工具和容器数据放D盘，Docker Desktop不是运行前提。Windows中查看引擎：`wsl -d Debian -u root -- docker info`。不要删除 `data/local/wsl-debian`，它是本机正在使用的WSL磁盘；软件包、镜像缓存、密钥和任务数据均不提交Git。

## 启动、停止与测试

在项目根目录执行。若整套服务已运行，直接运行测试；下列完整启动顺序用于重启后端口均空闲的情况：

```powershell
cd 'D:\软工\agent'
powershell -NoProfile -ExecutionPolicy Bypass -File infra/start-local.ps1 -MockAgents -WithBackend
# 仅把8200从模拟服务切换成真实服务，保留8000/8001/8100。
powershell -NoProfile -ExecutionPolicy Bypass -File infra/stop-local.ps1 -Service sandbox
powershell -NoProfile -ExecutionPolicy Bypass -File infra/start-sandbox.ps1 -WslDistro Debian
& .\.venv\Scripts\python.exe -m gateway.smoke_sandbox --wsl-distro Debian
& .\.venv\Scripts\python.exe -m pytest backend/tests gateway/tests -q
```

网关Swagger为 http://127.0.0.1:8000/docs ，业务Swagger为 http://127.0.0.1:8001/docs 。先按 `backend/README.md` 登录，用JWT调用 `POST /api/code-tests`，取任务号后查询 `GET /api/tasks/{task_id}`。领域结果在 `result.status`；任务完成不等于断言通过。启动脚本会启动WSL内的Docker服务，并复用已构建镜像。

停止本项目全部HTTP服务：`powershell -NoProfile -ExecutionPolicy Bypass -File infra/stop-local.ps1`。仅重启真实沙箱时，先加 `-Service sandbox` 停止，再执行 `start-sandbox.ps1 -WslDistro Debian`；无需重启业务或网关。进程记录兼容Windows PowerShell 5.1，替换沙箱记录后不会保留旧模拟PID。

WSL模式下，启动脚本另保存项目级隐藏保活进程，防止Linux后台Docker在Windows客户端退出后随发行版被停掉；`stop-local.ps1 -Service sandbox`同时回收该保活进程。HTTP API容器打包、知识模型接入及PostgreSQL演练见 [部署补充](1021-knowledge-and-deploy-v1.md)。

## 真实验证结果

2026-10-21 19:22（北京时间）执行的8组场景全部通过，使用真实Docker，不接受 `mock=true`：

| 场景 | 结果 |
|---|---|
| 正确加法及断言 | SUCCESS，1项通过，行覆盖率100% |
| 未调用的学生辅助类 | SUCCESS，行覆盖率50%，验证汇总范围包含全部学生类 |
| 错误断言 | FAILED，1项失败 |
| Java语法错误 | COMPILE_ERROR |
| 没有JUnit测试 | NO_TESTS |
| 测试内死循环 | TIMEOUT；测试JVM5秒强杀，包含编译、调用和轮询的总耗时约9.6秒 |
| 访问外部网络 | 连接失败的断言通过，容器NetworkMode=none |
| 输出反斜杠、制表符和引号 | JSON可正常解析、原内容可读 |

验证程序在死循环容器运行时真实inspect确认：256MiB内存、1 CPU、64 PID上限、只读根文件系统、丢弃全部capabilities、no-new-privileges。所有测试结束后，无 `zhigou.sandbox=local` 标签容器，无 `agents/sandbox/tmp/task-*` 目录残留。脱敏证据含时间、任务号、计数、覆盖率、隔离参数和回收结果，保存在忽略目录 `data/local/sandbox-smoke.json`，不含Token。业务与网关的63项自动测试也通过。

## 必要修复与文件边界

保留工程同学原 `SandboxServer.java`、`SandboxRunner.java` 和 `Dockerfile`，新增 `SandboxServerLocal.java` 本地入口：

- 临时目录由启动脚本指定，Windows路径经wslpath转换后挂入容器，不依赖队友电脑的固定路径；HTTP仅监听127.0.0.1:8200。
- 测试JVM使用5秒墙钟限制，Docker进程最多等待60秒，之后按唯一容器名限时回收。输出线程持续消费日志，最多保留末尾65536个字符，避免先读输出再等待造成无限阻塞。
- 显式使用任务UUID作为JaCoCo sessionid。原自动会话标识会调用主机名解析，在无网络容器内额外等待约5秒；修复后仍保持5秒上限。JaCoCo不插桩JUnit框架，覆盖率报告只统计学生编译目录并取汇总LINE计数。
- 短时JUnit任务使用 `-XX:TieredStopAtLevel=1` 减少JIT启动成本，不放宽5秒墙钟限制。请求日志沿用X-Request-ID并记录耗时，便于区分接口、编译与测试问题。
- 请求最多1MiB；只接收代码和测试文本，处理JSON控制字符和小数格式。ZIP、图代码一致性仍未实现，不作已完成声明。

协议仍是 `POST /sandbox/run-tests`、text/plain、`代码 + 换行---TEST---换行 + 测试`，返回status/tests/coverage/output。OPTIONS 204只证明HTTP路由可达；必须执行上述真实测试才算验证执行器。

## 依赖下载与离线构建

本机Windows代理仅监听回环端口，WSL NAT中部分仓库域名解析为不可达地址，因此采用现有Windows代理下载官方镜像及Maven Central制品，再离线导入构建。没有改全局WSL网络配置、代理监听或防火墙。

基础镜像来自官方 `library/eclipse-temurin`，逐层校验SHA256；linux/amd64 manifest为 `sha256:48a14534db535cda2c17f8fe5f10df388f9bd2b2de5f674e152d9f9e279036bd`。JUnit/JaCoCo制品用官方仓库校验和核对，SHA256记录于 `data/local/sandbox-image/artifacts.json`。`Dockerfile.local`只复制这些已验证JAR，与原交付使用相同版本。

本机缓存完整时可离线重建：

```powershell
Copy-Item agents/sandbox/Dockerfile.local data/local/sandbox-image/Dockerfile -Force
wsl -d Debian -u root -- docker load -i '/mnt/d/软工/agent/data/local/tools/temurin17-oci.tar'
wsl -d Debian -u root -- docker build --pull=false --network=none -t zhigou-sandbox-jdk17:v1 '/mnt/d/软工/agent/data/local/sandbox-image'
```

其他电脑网络正常时可直接用原Dockerfile构建，不依赖本机缓存。专用Debian中可运行 `infra/install-docker-wsl.sh` 安装官方Docker Engine；它面向全新Debian 12/13，不删除既有软件或数据。安装依据：[Docker官方Debian说明](https://docs.docker.com/engine/install/debian/)。JaCoCo等待原因可查 [0.8.12 Agent源码](https://github.com/jacoco/jacoco/blob/v0.8.12/org.jacoco.agent.rt/src/org/jacoco/agent/rt/internal/Agent.java)。

知识Agent仍为8100模拟服务，前端、生产PostgreSQL整套部署、公网HTTPS及线上故障演练仍待对应服务和服务器。本地入口只用于已验证的本地联调，不直接暴露到公网。
