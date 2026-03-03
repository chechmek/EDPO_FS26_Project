from __future__ import annotations

import json
import threading
from uuid import UUID

import psycopg
from confluent_kafka import Message
from psycopg.errors import UniqueViolation

from app.config import settings
from shared.events import (
    CommentAddedPayload,
    PostCreatedPayload,
    PostLikedPayload,
    parse_typed_event,
)
from shared.kafka import build_consumer


class NotificationConsumerWorker:
    def __init__(self, logger) -> None:
        self._logger = logger
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="notification-consumer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        consumer = build_consumer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_group_id,
            client_id=settings.service_name,
            auto_offset_reset=settings.kafka_auto_offset_reset,
        )
        topics = [settings.post_events_topic, settings.interaction_events_topic]
        consumer.subscribe(topics)
        self._logger.info("consumer_started", topics=topics)

        try:
            while not self._stop_event.is_set():
                msg = consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    self._logger.error("consumer_error", error=str(msg.error()))
                    continue

                should_commit = self._process_message(msg)
                if should_commit:
                    consumer.commit(message=msg, asynchronous=False)
        finally:
            consumer.close()
            self._logger.info("consumer_stopped")

    def _process_message(self, msg: Message) -> bool:
        try:
            raw = json.loads(msg.value().decode("utf-8"))
            envelope, payload = parse_typed_event(raw)
        except Exception as exc:
            # Invalid events are skipped so a bad message does not block the partition.
            self._logger.error("event_validation_failed", error=str(exc))
            return True

        try:
            with psycopg.connect(settings.db_dsn) as conn:
                with conn.cursor() as cur:
                    if isinstance(payload, PostCreatedPayload):
                        cur.execute(
                            """
                            INSERT INTO post_owners (post_id, author_id, updated_at)
                            VALUES (%s, %s, NOW())
                            ON CONFLICT (post_id)
                            DO UPDATE SET author_id = EXCLUDED.author_id,
                                          updated_at = NOW()
                            """,
                            (payload.post_id, payload.author_id),
                        )
                        conn.commit()
                        self._logger.info("post_owner_upserted", post_id=str(payload.post_id), author_id=str(payload.author_id))
                        return True

                    notification = self._to_notification(cur, payload)
                    if notification is None:
                        self._logger.info("event_ignored", event_type=envelope.type)
                        conn.commit()
                        return True

                    cur.execute(
                        """
                        INSERT INTO notifications (user_id, type, message, payload, source_event_id)
                        VALUES (%s, %s, %s, %s::jsonb, %s)
                        """,
                        (
                            notification["user_id"],
                            notification["type"],
                            notification["message"],
                            json.dumps(notification["payload"]),
                            envelope.event_id,
                        ),
                    )
                conn.commit()
            self._logger.info("notification_created", event_id=str(envelope.event_id), event_type=envelope.type)
            return True
        except UniqueViolation:
            self._logger.info("duplicate_event_ignored", event_id=str(envelope.event_id))
            return True
        except Exception as exc:
            self._logger.error("notification_processing_failed", error=str(exc))
            return False

    @staticmethod
    def _lookup_post_owner(cur, post_id: UUID) -> UUID | None:
        cur.execute("SELECT author_id FROM post_owners WHERE post_id = %s", (post_id,))
        row = cur.fetchone()
        return None if row is None else row[0]

    def _to_notification(self, cur, payload):
        if isinstance(payload, PostLikedPayload):
            owner_id = self._lookup_post_owner(cur, payload.post_id)
            if owner_id is None:
                self._logger.warning("post_owner_missing", post_id=str(payload.post_id), event_type="PostLiked")
                return None
            if owner_id == payload.user_id:
                return None
            return {
                "user_id": owner_id,
                "type": "PostLiked",
                "message": "Your post was liked",
                "payload": payload.model_dump(mode="json"),
            }
        if isinstance(payload, CommentAddedPayload):
            owner_id = self._lookup_post_owner(cur, payload.post_id)
            if owner_id is None:
                self._logger.warning("post_owner_missing", post_id=str(payload.post_id), event_type="CommentAdded")
                return None
            if owner_id == payload.user_id:
                return None
            return {
                "user_id": owner_id,
                "type": "CommentAdded",
                "message": "Your post was commented",
                "payload": payload.model_dump(mode="json"),
            }
        return None
