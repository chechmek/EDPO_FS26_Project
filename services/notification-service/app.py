"""
Notification Service
====================
Stores notifications in memory.

It supports two ingress paths:
  - Kafka topics emitted by Camunda Kafka connectors
  - direct internal HTTP writes from other services
"""

import json
import logging
import os
import threading

from confluent_kafka import Consumer, KafkaError
from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("notification-service")

app = Flask(__name__)

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "notification-service")
TOPICS = ["objection-approved", "post-deleted", "user-registered", "user-rejected"]

_notifications: dict[str, list] = {}
_lock = threading.Lock()


def _store(user_id: str, notif_type: str, message: str, payload: dict):
    with _lock:
        _notifications.setdefault(user_id, []).append(
            {
                "type": notif_type,
                "message": message,
                "payload": payload,
            }
        )


def handle_objection_approved(payload: dict):
    post_owner_id = payload.get("postOwnerId")
    report_id = payload.get("reportId")
    post_id = payload.get("postId")
    log.info("[objection-approved] postOwnerId=%s postId=%s reportId=%s", post_owner_id, post_id, report_id)
    if post_owner_id:
        _store(
            post_owner_id,
            "objection-approved",
            "Your objection to the content report was upheld. Your post remains visible.",
            payload,
        )


def handle_post_deleted(payload: dict):
    post_owner_id = payload.get("postOwnerId")
    report_id = payload.get("reportId")
    post_id = payload.get("postId")
    log.info("[post-deleted] postOwnerId=%s postId=%s reportId=%s", post_owner_id, post_id, report_id)
    if post_owner_id:
        _store(
            post_owner_id,
            "post-deleted",
            "Your post was removed following a content moderation review.",
            payload,
        )


def handle_user_registered(payload: dict):
    user_id = payload.get("userId")
    log.info("[user-registered] userId=%s", user_id)
    if user_id:
        _store(
            user_id,
            "user-registered",
            "Your registration was approved. Welcome!",
            payload,
        )


def handle_user_rejected(payload: dict):
    user_id = payload.get("userId")
    log.info("[user-rejected] userId=%s", user_id)
    if user_id:
        _store(
            user_id,
            "user-rejected",
            "Your registration was not approved following the background check.",
            payload,
        )


HANDLERS = {
    "objection-approved": handle_objection_approved,
    "post-deleted": handle_post_deleted,
    "user-registered": handle_user_registered,
    "user-rejected": handle_user_rejected,
}


def _consume():
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


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "notification-service"})


@app.post("/internal/notifications")
def create_notification():
    body = request.get_json(force=True)
    user_id = body.get("userId")
    notif_type = body.get("type")
    message = body.get("message")
    if not user_id or not notif_type or not message:
        return jsonify({"error": "userId, type, and message are required"}), 400

    payload = body.get("payload", {})
    _store(user_id, notif_type, message, payload)
    return jsonify({"stored": True}), 201


@app.get("/notifications/<user_id>")
def get_notifications(user_id):
    with _lock:
        notifs = list(_notifications.get(user_id, []))
    return jsonify({"userId": user_id, "notifications": notifs})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8005)
