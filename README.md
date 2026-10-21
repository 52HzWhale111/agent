# 知构 · 软件工程学习智能体

面向《软件工程》课程的学习智能体：帮学生把“需求说不清、图不会画、文档写不实、测试做不全”变成“知道每一步该交什么、哪里还不合格”。

> 这个仓库只用来放文件：代码、文档、设计稿、题库、部署脚本。
> 任务分工、截止时间、每天进度在群里说，不写进仓库。

## 目录

| 目录 | 放什么 |
| --- | --- |
| `frontend/` | 前端代码；`app/` 放页面，`components/uml/` 放 UML 画布 |
| `backend/` | 业务接口、数据库脚本、测试数据 |
| `gateway/` | 接入层：调用 Agent、队列、文件上传 |
| `agents/knowledge/` | 问答、需求拆解、文档评审的代码和提示词 |
| `agents/sandbox/` | 编译、JUnit、覆盖率、图代码对比 |
| `knowledge-base/` | 知识点、案例、文档模板、示例图、代码任务 |
| `docs/` | 需求文档、接口约定、UML 数据格式、设计稿、部署说明、测试清单 |
| `infra/` | docker-compose、Nginx、部署脚本 |

每个目录下的 `README.md` 写了这里该放什么，传之前看一眼。

## 怎么传文件

1. 打开仓库页面 → **Add file → Upload files**；
2. 把文件或文件夹拖进虚线框（文件夹会保留目录结构）；
3. 下面提交信息写清楚，比如 `上传: 问答页设计稿 v2`；
4. 点 **Commit changes**。

下载单个文件：点开文件 → 右上角 **Download raw file**。  
下载整个仓库：绿色 **Code** 按钮 → **Download ZIP**。

## 文件命名

统一用：`日期-内容-版本`

- `1018-问答页设计稿-v2.png`
- `1018-接口约定-v1.md`
- `1020-失物招领案例.docx`

**不要覆盖别人的文件**。要改就在自己的文件名后加版本号，旧的留着，出问题能退回去。

## 不要传的东西

- `.env`、API Key、数据库密码；
- 教材 PDF、老师课件原文件；
- 视频、超过 50MB 的 PPT 或数据集——放网盘，群里发链接。

## 本地运行

> 等前后端定了技术栈再补，先占个位置。

```bash
cp .env.example .env
# 前端
cd frontend && npm install && npm run dev
# 后端
cd backend && pip install -r requirements.txt && uvicorn main:app --reload
```

## 常用文档

- 接口字段：`docs/接口约定.md`
- UML 图数据格式：`docs/uml-schema.md`
- 部署说明：`docs/deploy.md`
- 设计稿目录：`docs/design/`
- 测试清单目录：`docs/testing/`

## 后端1业务接口

业务接口、数据库初始化、演示账号和测试步骤见 [后端运行与交接说明](backend/README.md)。相关需求、接口及UML数据约定由该说明统一链接。
