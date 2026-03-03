FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY shared /app/shared
COPY services/interaction-service /app/services/interaction-service

RUN uv pip install --system /app/shared /app/services/interaction-service

WORKDIR /app/services/interaction-service

EXPOSE 8002

CMD ["uv", "run", "--no-project", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
