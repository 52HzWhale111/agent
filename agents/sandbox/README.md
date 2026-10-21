# agents/sandbox (Agent 工程 - 代码沙箱服务)

负责接收学生代码，在隔离的 Docker 环境中编译、运行 JUnit 测试，并计算 JaCoCo 覆盖率。

## 1. 对外接口

- **端口**: `8200` (对应 `.env` 中的 `SANDBOX_AGENT_URL`)
- **路由**: `POST /sandbox/run-tests`
- **请求格式**: 纯文本 (text/plain)
  - 格式要求：`学生代码` + `\n---TEST---\n` + `测试代码`
  - *(注意：服务端会自动解析 `public class xxx` 提取类名并保存为 `.java` 文件)*

## 2. 返回结果 (JSON)

返回的 JSON 示例：

```json
{
  "status": "SUCCESS", 
  "tests": { "success": 1, "failed": 0 }, 
  "coverage": { "line": 100.0 }, 
  "output": "编译和运行的控制台输出..."
}
```



**状态 (status) 说明**：

- `SUCCESS`: 测试全过
- `FAILED`: 测试有失败
- `COMPILE_ERROR`: 编译报错（如少分号）
- `TIMEOUT`: 超时（超过5秒强制强杀）
- `ERROR`: 服务端内部异常

## 3. 本地启动

```powershell
# 1. 确保在 agents/sandbox 目录下
chcp 65001
mvn compile exec:java "-Dexec.mainClass=com.zhigou.SandboxServer"
```



## 4. 镜像与安全限制

- **基础镜像**: `zhigou-sandbox-jdk17:v1` (已预装 JUnit 1.10.2 与 JaCoCo 0.8.12)
- **安全限制**: 无网络 (`--network none`)、最大内存 256MB、最大 5 秒执行时间、单次请求最长 60 秒。

## 5. 目录结构

- `src/`: 沙箱 HTTP 服务源码
- `testcases/`: 坏代码及本地测试用例 (死循环、编译报错)
- `tmp/`: 运行时临时文件 (已在 .gitignore 中忽略)