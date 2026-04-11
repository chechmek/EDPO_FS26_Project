"""
Notification Service
====================
Kafka-only consumer that prints small notification messages to the console.
"""

import json
import logging
import os

from confluent_kafka import Consumer, KafkaError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("notification-service")

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "notification-service")
TOPICS = [
    "objection-approved",
    "post-deleted",
    "user-registered",
    "user-rejected",
    "report-notification",
    "verification-notification",
]


def _handle_objection_approved(payload: dict) -> None:
    log.info(
        "[notify] topic=objection-approved userId=%s postId=%s reportId=%s",
        payload.get("postOwnerId"),
        payload.get("postId"),
        payload.get("reportId"),
    )


def _handle_post_deleted(payload: dict) -> None:
    log.info(
        "[notify] topic=post-deleted userId=%s postId=%s reportId=%s",
        payload.get("postOwnerId"),
        payload.get("postId"),
        payload.get("reportId"),
    )


def _handle_user_registered(payload: dict) -> None:
    log.info("[notify] topic=user-registered userId=%s", payload.get("userId"))


def _handle_user_rejected(payload: dict) -> None:
    log.info("[notify] topic=user-rejected userId=%s", payload.get("userId"))


def _handle_verification_notification(payload: dict) -> None:
    user_id = payload.get("userId")
    notif_type = payload.get("type")
    message = payload.get("message")
    verification_id = (payload.get("payload") or {}).get("verificationId")
    log.info(
        "[notify] topic=verification-notification userId=%s type=%s verificationId=%s message=%s",
        user_id,
        notif_type,
        verification_id,
        message,
    )


def _handle_report_notification(payload: dict) -> None:
    user_id = payload.get("userId")
    notif_type = payload.get("type")
    message = payload.get("message")
    report_id = (payload.get("payload") or {}).get("reportId")
    log.info(
        "[notify] topic=report-notification userId=%s type=%s reportId=%s message=%s",
        user_id,
        notif_type,
        report_id,
        message,
    )


HANDLERS = {
    "objection-approved": _handle_objection_approved,
    "post-deleted": _handle_post_deleted,
    "user-registered": _handle_user_registered,
    "user-rejected": _handle_user_rejected,
    "report-notification": _handle_report_notification,
    "verification-notification": _handle_verification_notification,
}


def main() -> None:
    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": KAFKA_GROUP_ID,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe(TOPICS)
    log.info("Kafka consumer started, subscribed to %s on %s", TOPICS, KAFKA_BOOTSTRAP_SERVERS)

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue

            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    log.error("Kafka error: %s", msg.error())
                continue

            topic = msg.topic()
            raw_value = msg.value() or b"{}"
            try:
                payload = json.loads(raw_value.decode("utf-8"))
            except Exception:
                log.exception("Failed to decode Kafka message topic=%s", topic)
                consumer.commit(message=msg)
                continue

            handler = HANDLERS.get(topic)
            if handler is None:
                log.info("[notify] topic=%s payload=%s", topic, payload)
            else:
                try:
                    handler(payload)
                except Exception:
                    log.exception("Handler failed for topic=%s", topic)

            consumer.commit(message=msg)
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
