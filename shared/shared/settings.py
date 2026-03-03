from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    service_name: str = "service"
    log_level: str = "INFO"

    db_dsn: str

    kafka_bootstrap_servers: str = "kafka:9092"
    post_events_topic: str = "social.post.events"
    interaction_events_topic: str = "social.interaction.events"
    notification_events_topic: str = "social.notification.events"

    kafka_group_id: str = "notification-service"
    kafka_auto_offset_reset: str = "earliest"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
