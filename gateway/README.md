# 接入层

后端2交付入口：

- `main.py`：Agent转发、持久化任务、上传、追踪日志、健康检查。
- `requirements.txt` / `Dockerfile`：运行依赖与容器。
- `tests/`：使用HTTP测试替身验证接入行为，不代表真实模型/沙箱验收。
- [协议与本地启动](../docs/agent-integration.md)
- [部署、备份与监控](../docs/deploy.md)
- [需求及验收贡献](../docs/backend2-acceptance.md)

未修改他人PR、知识资料或原有README；任务只涵盖后端2职责。
