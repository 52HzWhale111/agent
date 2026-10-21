"""
Agent 知识服务接口骨架
三条链路：概念问答、需求拆解、文档评审
用 FastAPI 写，后端2直接来接就行
"""

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI(title="知构 - Agent知识服务")


# ========== 数据模型 ==========

class QAQuery(BaseModel):
    """概念问答请求"""
    question: str
    user_id: Optional[str] = None


class QAResponse(BaseModel):
    """概念问答响应"""
    answer: str
    sources: List[str]  # 出处列表
    retrieved_count: int  # 检索到了几条资料


class RequirementQuery(BaseModel):
    """需求拆解请求"""
    description: str  # 学生输入的需求描述


class RequirementResponse(BaseModel):
    """需求拆解响应"""
    actors: List[dict]
    use_cases: List[dict]
    main_flow: List[str]
    exception_flow: List[dict]
    notes: str


class DocumentReviewQuery(BaseModel):
    """文档评审请求"""
    content: str  # 文档全文
    doc_type: str  # 文档类型：srs(需求规格)/hld(概要设计)/test_report(测试报告)


class Issue(BaseModel):
    location: str
    type: str  # missing_section / empty_words / mismatch
    description: str
    suggestion: str


class DocumentReviewResponse(BaseModel):
    """文档评审响应"""
    overall_comment: str
    issues: List[Issue]
    issue_count: dict


# ========== 接口 ==========

@app.post("/qa", response_model=QAResponse)
async def concept_qa(query: QAQuery):
    """
    概念问答链路
    1. 调用检索器，从知识库找出最相关的3-5条知识点
    2. 把知识点 + 学生问题塞进Prompt
    3. 调用大模型API
    4. 返回答案 + 出处
    """
    # TODO: 实际实现时，这里调用 knowledge_retriever.retrieve()
    # 然后调用大模型API，把结果返回

    # 示例返回（占位）
    return QAResponse(
        answer="这里是大模型生成的回答...",
        sources=["第5章 模块独立性", "第5章 内聚性", "第5章 耦合性"],
        retrieved_count=3
    )


@app.post("/analyze_requirement", response_model=RequirementResponse)
async def analyze_requirement(query: RequirementQuery):
    """
    需求拆解链路
    1. 把学生的需求描述塞进Prompt
    2. 调用大模型API，要求输出JSON
    3. 解析JSON，返回结构化结果
    """
    # TODO: 实际实现时，这里调用大模型API，用需求拆解的Prompt模板

    # 示例返回（占位）
    return RequirementResponse(
        actors=[{"name": "学生", "description": "普通用户"}],
        use_cases=[{"name": "发布失物", "description": "提交物品信息"}],
        main_flow=["第一步：登录", "第二步：发布物品"],
        exception_flow=[{"name": "重复发布", "description": "防止刷数据", "处理方式": "冷却时间"}],
        notes="这是示例返回"
    )


@app.post("/review_document", response_model=DocumentReviewResponse)
async def review_document(query: DocumentReviewQuery):
    """
    文档评审链路
    1. 根据doc_type选择对应的模板评审点
    2. 把文档内容 + 模板塞进Prompt
    3. 调用大模型API
    4. 返回问题清单
    """
    # TODO: 实际实现时，这里调用大模型API，用文档评审的Prompt模板

    # 示例返回（占位）
    return DocumentReviewResponse(
        overall_comment="文档基本完整，但有些地方需要补充。",
        issues=[
            Issue(
                location="第3章 3.2节",
                type="empty_words",
                description="'系统性能要好'没有量化指标",
                suggestion="改成'响应时间不超过2秒'"
            )
        ],
        issue_count={"missing_section": 1, "empty_words": 2, "mismatch": 0}
    )


@app.get("/health")
async def health_check():
    """健康检查，后端2用来判断服务是否正常"""
    return {"status": "ok", "service": "agent-knowledge"}


# ========== 启动 ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
