"""Local test double only. Never use this app as a production Agent."""
import asyncio
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

service = os.getenv("MOCK_SERVICE", "knowledge")
app = FastAPI(title=f"MOCK {service} — 本地接入测试替身，不执行真实 Agent 任务")


class Behavior(BaseModel):
    delay_seconds: float = Field(default=2, ge=0, le=180)
    unavailable: bool = False


behavior = Behavior()


@app.post("/__test__/behavior")
async def configure(value: Behavior):
    global behavior
    behavior = value
    return {"mock": True, **behavior.model_dump()}


@app.get("/health")
async def health():
    return JSONResponse({"mock": True, "service": service,
                         "status": "unavailable" if behavior.unavailable else "ok"},
                        status_code=503 if behavior.unavailable else 200)


@app.post("/{operation}")
async def execute(operation: str, request: Request):
    allowed = {"qa", "analyze_requirement", "review_document"} if service == "knowledge" else {"run_tests", "check_consistency"}
    if operation not in allowed:
        return JSONResponse({"mock": True, "error": "Unknown test operation"}, status_code=404)
    if behavior.unavailable:
        return JSONResponse({"mock": True, "error": "Simulated outage"}, status_code=503)
    await asyncio.sleep(behavior.delay_seconds)
    payload = await request.json()
    result = {"mock": True, "notice": "仅用于验证接入流程；未调用模型、未执行编译或测试。",
              "request_id": request.headers.get("X-Request-ID"), "operation": operation}
    if operation == "qa":
        result.update(answer="【Mock】接入层已转发问题：" + str(payload.get("question", "")), sources=[], retrieved_count=0)
    elif operation == "analyze_requirement":
        result.update(actors=[], use_cases=[], main_flow=[], exception_flow=[], notes="Mock，不生成真实需求分析")
    elif operation == "review_document":
        result.update(overall_comment="【Mock】文档正文已到达测试替身。", issues=[], issue_count={},
                      received_characters=len(payload.get("content", "")))
    elif operation == "run_tests":
        result.update(compiled=None, passed=None, failed=None, coverage=None,
                      archive_received=bool(payload.get("archive_path")))
    else:
        result.update(issues=[])
    return result
