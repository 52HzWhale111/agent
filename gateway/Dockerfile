FROM python:3.12-slim
WORKDIR /app
COPY gateway/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home gateway
COPY gateway /app/gateway
COPY infra/gateway_backup.py /app/infra/gateway_backup.py
RUN mkdir -p /data && chown gateway:gateway /data
USER gateway
ENV GATEWAY_DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
