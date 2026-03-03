FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY shared /app/shared
COPY services/notification-service /app/services/notification-service

RUN uv pip install --system /app/shared /app/services/notification-service

WORKDIR /app/services/notification-service

EXPOSE 8003

CMD ["uv", "run", "--no-project", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
