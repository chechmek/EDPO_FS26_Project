"""
Notification Service
====================
Kafka subscriber that reacts to content moderation outcomes emitted by
Camunda's Kafka outbound connectors in the ReportContent.bpmn process.

Topics consumed:
  - objection-approved   → post owner's objection was upheld; post remains
  - post-deleted         → post was removed after moderation

Each event creates a notification record for the post owner.

REST endpoints:
  GET /notifications/<user_id>   → list notifications for a user
  GET /health
"""

import json
import logging
import os
import threading

from confluent_kafka import Consumer, KafkaError
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("notification-service")

app = Flask(__name__)

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "notification-service")
TOPICS = ["objection-approved", "post-deleted"]

# In-memory store: user_id → [{ type, message, payload }]
# Replace with DB inserts into the notifications table in production.
_notifications: dict[str, list] = {}
_lock = threading.Lock()


def _store(user_id: str, notif_type: str, message: str, payload: dict):
    with _lock:
        _notifications.setdefault(user_id, []).append({
            "type": notif_type,
            "message": message,
            "payload": payload,
        })


# ---------------------------------------------------------------------------
# Event handlers — one per subscribed topic
# ---------------------------------------------------------------------------

def handle_objection_approved(payload: dict):
    """
    The post owner's objection was upheld by the moderator.
    Their post was not deleted.
    Expected payload: { reportId, postId, postOwnerId }
    """
    post_owner_id = payload.get("postOwnerId")
    post_id = payload.get("postId")
    report_id = payload.get("reportId")
    log.info("[objection-approved] postOwnerId=%s postId=%s reportId=%s",
             post_owner_id, post_id, report_id)
    if post_owner_id:
        _store(
            post_owner_id,
            "objection-approved",
            "Your objection to the content report was upheld. Your post remains visible.",
            payload,
        )


def handle_post_deleted(payload: dict):
    """
    A post was removed after moderation — either the deadline passed with
    no objection, or the moderator rejected the objection.
    Expected payload: { reportId, postId, postOwnerId }
    """
    post_owner_id = payload.get("postOwnerId")
    post_id = payload.get("postId")
    report_id = payload.get("reportId")
    log.info("[post-deleted] postOwnerId=%s postId=%s reportId=%s",
             post_owner_id, post_id, report_id)
    if post_owner_id:
        _store(
            post_owner_id,
            "post-deleted",
            "Your post was removed following a content moderation review.",
            payload,
        )


HANDLERS = {
    "objection-approved": handle_objection_approved,
    "post-deleted": handle_post_deleted,
}


# ---------------------------------------------------------------------------
# Kafka consumer loop
# ---------------------------------------------------------------------------

def _consume():
    consumer = Consumer({
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "group.id": KAFKA_GROUP_ID,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    })
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
            try:
                payload = json.loads(msg.value().decode("utf-8"))
            except Exception:
                log.exception("Failed to decode message on topic %s", topic)
                consumer.commit(message=msg)
                continue

            handler = HANDLERS.get(topic)
            if handler:
                try:
                    handler(payload)
                except Exception:
                    log.exception("Handler raised for topic %s", topic)

            consumer.commit(message=msg)
    finally:
        consumer.close()


threading.Thread(target=_consume, daemon=True, name="kafka-consumer").start()


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "notification-service"})


@app.get("/notifications/<user_id>")
def get_notifications(user_id):
    with _lock:
        notifs = list(_notifications.get(user_id, []))
    return jsonify({"userId": user_id, "notifications": notifs})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8005)
