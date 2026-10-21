# 知识 Agent 运行与接入

从主仓库 `Agent知识任务交付包.zip`（main提交 `f487884`，Git blob `18d0cde6ab4757a07b9649d52a7c02b12696d402`）解出原始10个文件。Prompt、60点检索器、20个案例及原 `agent_api.py` 原样保留；原总说明另存为 `DELIVERY_README.md`。原接口有固定占位响应，**运行入口使用新增 `api.py`**。

## 模型配置

本次选择 DeepSeek 官方 **DeepSeek-V4.1-Flash-1017**，2026-10-21官方文档要求使用API名称 `deepseek-flash`，非思考模式。该名称是服务商别名，后续可能更新指向；正式评测时记录响应中的实际模型标识、日期及配置。将以下内容填入忽略文件 `data/local/knowledge.env`，API Key 只在本地保存：

```dotenv
LLM_API_KEY=你的官方API_Key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
```

首次安装依赖：`.\.venv\Scripts\python.exe -m pip install -r agents/knowledge/requirements.txt`。在项目根目录启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infra/stop-local.ps1 -Service knowledge
powershell -NoProfile -ExecutionPolicy Bypass -File infra/start-knowledge.ps1
& .\.venv\Scripts\python.exe -m gateway.smoke_knowledge
```

完整重启业务、网关、工程沙箱的顺序见 [本地运行说明](../../docs/1021-sandbox-local-v1.md)，最后将8100模拟服务替换成本服务。Swagger为 http://127.0.0.1:8100/docs；前端统一调用8000网关，携带后端1登录JWT。

## 契约与实现范围

| 接口 | 输入 | 输出 |
|---|---|---|
| `POST /qa` | `question`，可选 `user_id` | `answer`、`sources`、`retrieved_count` |
| `POST /analyze_requirement` | `description` | `actors`、`use_cases`、`main_flow`、`exception_flow`、`notes` |
| `POST /review_document` | `content`、`doc_type`=`srs/hld/test_report` | `overall_comment`、`issues`、`issue_count` |

- 问答使用原关键词检索器，最多5条；模型引用编号必须属于本次检索结果，出处文字由服务端重建。没有召回时直接说明资料不足，不调用模型。
- 需求拆解与评审沿用原Prompt的第一段完整模板，用户输入独立放在user消息。评审读取知识工程对应TPL文件；中文问题类型转换为 `missing_section/empty_words/mismatch`，计数由实际issues重新计算。
- 所有模型输出做JSON与字段校验；格式或引用不合格最多重试一次，总预算问答/拆解24秒、评审50秒；不会返回固定成功示例。
- 评审正文上限60000字符，超出返回422，需拆分或缩短；当前仅分析已提取文字，不识别文档内的图像。
- `X-Request-ID`沿用网关请求号，日志只记录接口、状态和耗时，不记录Key、Token、学生正文或完整模型返回。
- `/health`缺配置返回503；配置齐全只表示 `configured_inference_not_probed`，不会为健康探测额外扣模型额度。应跑真实调用脚本确认网络、额度和模型输出。

## 验证边界

新增6项协议回归覆盖三条链路、Prompt填充、计数纠正、缺配置、未知知识与非法模型输出/伪造引用。测试使用模型替身，不能写成真实模型验收。2026-10-21本机已配置真实Key并跑通问答、拆解和上传评审三条链路；`python -m agents.knowledge.evaluate_cases`已跑完原20案例，结果保存在忽略目录。发现QA-06/07资料/检索缺口及评审标准版本冲突、误报，见 [真实验证记录](../../docs/1021-knowledge-validation-v1.md)。知识/测试同学仍需统一标准后人工验收，当前不宣称检出率、误报率或领域能力提升已达标。

Docker构建：`docker build -f agents/knowledge/Dockerfile -t zhigou-knowledge:local .`。Compose已包含构建入口；Key由服务器`.env`注入，知识端口仅在容器网络内使用。

模型参数依据：[官方接入与模型](https://api-docs.deepseek.com/quick_start/pricing/)、[非思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。
