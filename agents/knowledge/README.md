# 知识 Agent 运行与接入

从主仓库 `Agent知识任务交付包.zip`（main提交 `f487884`，Git blob `18d0cde6ab4757a07b9649d52a7c02b12696d402`）解出原始10个文件。Prompt、60点检索器、20个案例及原 `agent_api.py` 原样保留；原总说明另存为 `DELIVERY_README.md`。原接口有固定占位响应，**运行入口使用新增 `api.py`**。

## 模型配置

本次选择 DeepSeek 官方 **DeepSeek-V4.1-Flash-1017**，2026-10-21官方文档要求使用API名称 `deepseek-flash`。问答、需求拆解和评审候选使用非思考模式；按用户选择，最终评审复核启用 `thinking.type=enabled`、`reasoning_effort=high`，最多8192输出Token，两步仍共用50秒总预算。仅解析最终content，不保存或返回reasoning_content。该名称是服务商别名，后续可能更新指向；正式评测时记录日期及配置。将以下内容填入忽略文件 `data/local/knowledge.env`，API Key 只在本地保存：

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
| `POST /review_document` | `content`、`doc_type`=`srs/hld/test_report`，可选 `scope`=`auto/full/excerpt` | `overall_comment`、`issues`、`issue_count` |

- 问答使用原关键词检索器，最多5条；`retrieval.py`补回知识原文的易错点和出处，并加载两条带官方来源的 `SUP-01/02` 补充资料。原60个KP编号不变，SUP不冒充教材知识点，也不自动写入业务题库。来源为团队整理的教材页码标注时，尚未核验教材原文件；外部资料另附URL。正文不再让模型生成出处行，由服务端校验引用编号并生成真实来源。
- 需求拆解与评审沿用原Prompt的第一段完整模板，用户输入独立放在user消息。评审读取知识工程对应TPL文件并加 `review_rules.md`，先生成、再根据原文复核，删除无依据/仅格式类建议、合并重复项、检查漏报。两步共用50秒总预算。
- 评审内部引用服务端编号的原文非空行，校验其实际存在，避免模型重写引文后改变标点或拼接内容；缺章声明不能与已有标题冲突。最终结果不合格最多重试一次，仍不合格返回502，不把校验失败包装成“没有问题”。证据行号字段只在内部使用，公开issue仍为 `location/type/description/suggestion`，计数由实际issues重新计算。
- 分类兼容约定：`missing_section`表示当前范围内缺失的必需内容；`empty_words`表示已有内容空泛、不可检验或写“无”；`mismatch`表示有证据的图文、业务目标或设计逻辑冲突（如步骤当用例、组成关系误用继承、结论与缺陷状态冲突）。原模板的三类枚举不变。
- `scope=auto`默认按首行标题识别：完整需求规格/概要设计说明书或测试报告按full；含“节选/片段/摘录”或无法确定时按excerpt。需要明确查缺整份文档的章节时传full；局部用例/类图/时序图传excerpt，不要求补无关的完整文档章节。8000网关 `/api/reviews`支持同名可选字段，旧调用保持可用。
- 所有模型输出做JSON与字段校验；每步格式、引用或证据不合格最多重试一次，总预算问答/拆解24秒、完整评审50秒；不会返回固定成功示例。
- 评审正文上限60000字符，超出返回422，需拆分或缩短；当前仅分析已提取文字，不识别文档内的图像。
- `X-Request-ID`沿用网关请求号，日志只记录接口、状态和耗时，不记录Key、Token、学生正文或完整模型返回。
- `/health`缺配置返回503；配置齐全只表示 `configured_inference_not_probed`，不会为健康探测额外扣模型额度。应跑真实调用脚本确认网络、额度和模型输出。

## 验证边界

21项知识协议回归覆盖三条链路、资料补充、出处重建、计数纠正、缺配置、伪造引用、原文行号、已有标题及覆盖率选项冲突、范围与高强度二次复核。测试使用模型替身，不能写成真实模型验收。本机已配置真实Key；真实链路与原20案例、4份正常对照的复测结果见 [修复记录](../../docs/1021-knowledge-fixes-v1.md)。知识/测试同学仍需统一标准后人工验收，当前不宣称任意文档零误报、原清单检出率/误报率或领域能力提升已达标。

Docker构建：`docker build -f agents/knowledge/Dockerfile -t zhigou-knowledge:local .`。Compose已包含构建入口；Key由服务器`.env`注入，知识端口仅在容器网络内使用。

修复后复测：`python -m agents.knowledge.evaluate_cases --with-review-controls --output data/local/knowledge-cases-high.json`，运行原20案例及4份正常文档对照；DR案例配套标准以主仓库实际DEF的`标准清单.md`为准，报告记录其路径和SHA256，旧知识包的预期清单保留为历史版本。正常对照是团队自编验证材料，不是教师标准答案。

模型参数依据：[官方接入与模型](https://api-docs.deepseek.com/quick_start/pricing/)、[非思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。
