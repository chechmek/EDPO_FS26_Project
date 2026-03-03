from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import wait_for_db
from app.kafka_pub import InteractionEventPublisher
from app.routes import router
from shared.kafka import build_producer
from shared.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    logger = get_logger(settings.service_name)

    wait_for_db()
    producer = build_producer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        client_id=settings.service_name,
    )

    app.state.producer = producer
    app.state.publisher = InteractionEventPublisher(producer, settings.interaction_events_topic)

    logger.info("service_started", topic=settings.interaction_events_topic)
    try:
        yield
    finally:
        producer.flush(5)
        logger.info("service_stopped")


app = FastAPI(title="interaction-service", lifespan=lifespan)
app.include_router(router)
