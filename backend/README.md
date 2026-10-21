# 知构业务后端运行与交接说明

本目录提供后端1负责的账号、课程资料、文档保存、UML图版本、答题判分和薄弱点统计，以及建表SQL、接口契约和需求分析材料。业务服务可独立演示；Agent调用、队列、前端联调与生产部署由团队继续集成。

**下列命令均从仓库根目录执行。** Windows建议使用Python 3.12，本次测试也使用该版本；不要在`backend/`目录内重复执行根目录命令。

## 安装与启动

首次克隆后，在仓库根目录打开PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-lock.txt
.\.venv\Scripts\python.exe backend/scripts/manage.py init-db --demo
.\.venv\Scripts\python.exe -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
```

打开 [Swagger接口页面](http://127.0.0.1:8000/docs)。Ctrl+C停止服务，端口占用时将最后一条命令的端口改为8001。依赖准备完成后，也可从仓库根目录运行`.\run-local.ps1`；该脚本会幂等初始化演示数据并启动服务。

Linux使用以下命令：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-lock.txt
.venv/bin/python backend/scripts/manage.py init-db --demo
.venv/bin/python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
```

锁定文件包含本次验证的运行和测试依赖。`requirements.txt`为运行依赖范围，`requirements-dev.txt`增加测试依赖。

| 演示账号 | 演示密码 | 用途 |
| --- | --- | --- |
| demo_new | Demo@2026! | 无学习记录，展示空学情 |
| demo_student | Demo@2026! | 有正常答题与可编辑图数据 |
| demo_weak | Demo@2026! | KP-33、KP-34、KP-48有明确薄弱顺序 |

登录接口使用JSON；复制响应`data.access_token`，在Swagger右上Authorize粘贴后调用个人接口。演示数据只在显式`--demo`时创建，生产初始化不要使用该选项。

## 知识资料来源

知识工程原资料由独立的 [PR #1](https://github.com/52HzWhale111/agent/pull/1)管理，本次后端PR不重复提交原来的43份Markdown和PNG文件。初始化优先使用根目录`knowledge-base/`的知识点与需求案例，同时兼容早期资料包的`knowledge-base/knowledge-base/`布局；没有可用资料目录时，使用本目录的[data/knowledge_snapshot.json](data/knowledge_snapshot.json)导入同一批60个知识点和20个案例，因此后端分支可以独立运行，无需先合并或下载PR #1。

结构化快照保留资料ID、原文和来源说明，不代表教材页码已核验；埋雷文档不进入正常知识点或评分答案。18道选择练习是另外编制的示范题，明确标注为自编，供服务端判分和学情联调。

## 交接材料

- [后端需求与数据库设计](../docs/1021-后端需求与数据库设计-v1.md)：可合并到团队需求分析，含需求编号、用例、ER图、类图、非功能与验收。
- [接口字段与错误码](../docs/1021-接口约定-v1.md)、[OpenAPI文件](../docs/1021-openapi-v1.json)、[UML JSON约定](../docs/uml-schema.md)：供前端与接入层联调。
- [分工交接与验收](../docs/1021-分工交接与验收-v1.md)：协作边界和具体演示步骤。
- [sql/](sql/)：SQLite与PostgreSQL初始建表SQL；[data/](data/)：结构化知识快照、自编题、演示图及来源清单。
- [tests/](tests/)：接口与导入回归；当前PR在不含原资料目录的独立检出目录中完成35项测试，全部通过，具体结果见[测试记录](../docs/1021-后端测试记录-v1.md)。

本次PR提交后端业务代码与交接材料。团队真实用户调研、标志、前端原型、Agent定制效果对比、PPT与博客仍由对应成员汇总，不将后端实现等同于整组作业已完成。

## 配置与部署

默认数据库为`backend/data/zhigou.db`，上传目录为`backend/storage/`。开发密钥首次运行生成于`backend/.dev-jwt-secret`，使本地服务重启后仍可验证原Token。数据库、上传文件、密钥、虚拟环境和真实配置均由忽略规则排除。

[.env.example](.env.example)列出环境变量；程序直接读取进程环境，不会自动加载`.env`。生产部署示例仍从仓库根目录执行：

```powershell
$env:APP_ENV = 'production'
$env:DATABASE_URL = 'postgresql+psycopg://zhigou:YOUR_PASSWORD@localhost:5432/zhigou'
$env:JWT_SECRET_KEY = '替换为至少32字符的随机密钥'
$env:CORS_ORIGINS = 'https://你的前端域名'
.\.venv\Scripts\python.exe backend/scripts/manage.py init-db
.\.venv\Scripts\python.exe -m uvicorn main:app --app-dir backend --host 0.0.0.0 --port 8000
```

后端2负责注入真实配置、HTTPS、入口限制、Agent任务、备份和监控。[Dockerfile](Dockerfile)的构建上下文为仓库根目录。当前没有在Docker或真实PostgreSQL服务中完成运行验证；本地SQLite验证结果不能代替部署环境验收。初版可用ORM建表，后续结构变更需要显式迁移。

## 检查与导出

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe backend/scripts/manage.py export
```

导出命令从SQLAlchemy模型和FastAPI实现生成SQL及OpenAPI，避免手写字段偏移。SQL脚本针对空库，日常重复初始化使用`init-db`。重复导入更新课程条目，保留已有账号密码和已存在的演示事件，不清空学生作答。
