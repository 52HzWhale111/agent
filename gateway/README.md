# 接入层

后端2交付入口：

已适配主仓库后端1的登录、文档接口，以及工程PR #5的纯文本测试协议。联调方式见 [1021-后端2联调-v2](../docs/1021-后端2联调-v2.md)。

真实Docker沙箱已在本机验证，通过网关运行的8组场景及启动方式见 [真实沙箱本地运行](../docs/1021-sandbox-local-v1.md)。当前8200可切换为真实服务，8100知识Agent仍为模拟服务。

本地启动真实业务服务与Agent测试替身：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infra/start-local.ps1 -MockAgents -WithBackend
```

业务服务为8001端口，网关为8000端口。先在业务服务登录，取data.access_token，在网关Swagger的Authorize中填写；本模式会检查任务和文档的用户归属。Agent替身返回mock=true，不代表真正执行了模型或Java测试。

- `main.py`：Agent转发、持久化任务、上传、追踪日志、健康检查。
- `requirements.txt` / `Dockerfile`：运行依赖与容器。
- `tests/`：使用HTTP测试替身验证接入行为，不代表真实模型/沙箱验收。
- [协议与本地启动](../docs/agent-integration.md)
- [部署、备份与监控](../docs/deploy.md)
- [需求及验收贡献](../docs/backend2-acceptance.md)

未修改他人PR、知识资料或原有README；任务只涵盖后端2职责。
