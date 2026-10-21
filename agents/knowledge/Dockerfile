FROM python:3.12-slim
WORKDIR /app
COPY agents/knowledge/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home knowledge
COPY agents/knowledge /app/agents/knowledge
COPY knowledge-base/03_文档模板 /app/knowledge-base/03_文档模板
USER knowledge
ENV PYTHONUNBUFFERED=1
EXPOSE 8100
CMD ["uvicorn", "agents.knowledge.api:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
