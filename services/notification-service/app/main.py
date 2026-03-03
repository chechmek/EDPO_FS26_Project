from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import wait_for_db
from app.kafka_consumer import NotificationConsumerWorker
from app.routes import router
from shared.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    logger = get_logger(settings.service_name)

    wait_for_db()

    worker = NotificationConsumerWorker(logger)
    worker.start()
    app.state.consumer_worker = worker

    logger.info("service_started", topic=settings.interaction_events_topic)
    try:
        yield
    finally:
        worker.stop()
        logger.info("service_stopped")


app = FastAPI(title="notification-service", lifespan=lifespan)
app.include_router(router)
