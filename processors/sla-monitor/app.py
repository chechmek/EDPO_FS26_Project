from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
import threading
import time
from typing import Any

from confluent_kafka import Consumer, KafkaError, Producer
from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("sla-monitor")

app = Flask(__name__)

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "sla-monitor")
PORT = int(os.getenv("PORT", "8005"))
SLA_SECONDS = int(os.getenv("SLA_SECONDS", str(72 * 60 * 60)))
WINDOW_GRACE_SECONDS = int(os.getenv("WINDOW_GRACE_SECONDS", "10"))
WINDOW_RETENTION_MINUTES = int(os.getenv("WINDOW_RETENTION_MINUTES", "180"))
SCAN_INTERVAL_SECONDS = float(os.getenv("SCAN_INTERVAL_SECONDS", "1.0"))

INPUT_TOPICS = [
    "verification-notification",
    "report-notification",
    "post-deleted",
    "objection-approved",
]
VERIFICATION_METRICS_1M_TOPIC = "verification-metrics-1m"
VERIFICATION_METRICS_5M_HOP_TOPIC = "verification-metrics-5m-hop"
SLA_VIOLATIONS_TOPIC = "sla-violations"

VERIFICATION_STATUSES = [
    "verified",
    "rejected-peer",
    "rejected-internal",
    "rejected-unregistered",
    "timed-out",
]
SLA_OUTCOME_STATUSES = {"post-deleted", "objection-approved"}

_lock = threading.Lock()
_producer_lock = threading.Lock()
_producer: Producer | None = None
_consumer_ready = threading.Event()

_verification_metrics_1m: dict[str, dict[str, Any]] = {}
_verification_metrics_5m: dict[str, dict[str, Any]] = {}
_open_sla_cases: dict[str, dict[str, Any]] = {}
_sla_violations: dict[str, dict[str, Any]] = {}
_recent_sla_outcomes: dict[str, dict[str, Any]] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _parse_event_time(raw_value: str | None) -> datetime:
    if not raw_value:
        return _utc_now()
    normalized = raw_value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _floor_to_minute(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _window_key(window_start: datetime) -> str:
    return _isoformat(window_start)


def _default_counts() -> dict[str, int]:
    return {status: 0 for status in VERIFICATION_STATUSES}


def _build_window(window_start: datetime, *, duration_minutes: int, hop_size_seconds: int | None = None) -> dict[str, Any]:
    window_end = window_start + timedelta(minutes=duration_minutes)
    payload: dict[str, Any] = {
        "windowStart": _isoformat(window_start),
        "windowEnd": _isoformat(window_end),
        "counts": _default_counts(),
        "total": 0,
        "successRate": 0.0,
        "failureRate": 0.0,
        "emitted": False,
    }
    if hop_size_seconds is not None:
        payload["hopSizeSeconds"] = hop_size_seconds
        payload["windowSizeSeconds"] = duration_minutes * 60
    return payload


def _recalculate_rates(window: dict[str, Any]) -> None:
    counts = window["counts"]
    total = sum(counts.values())
    window["total"] = total
    if total == 0:
        window["successRate"] = 0.0
        window["failureRate"] = 0.0
        return
    success_count = counts.get("verified", 0)
    window["successRate"] = round(success_count / total, 4)
    window["failureRate"] = round((total - success_count) / total, 4)


def _get_producer() -> Producer:
    global _producer
    with _producer_lock:
        if _producer is None:
            _producer = Producer(
                {
                    "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
                    "client.id": "sla-monitor",
                    "acks": "all",
                    "enable.idempotence": True,
                }
            )
    return _producer


def _publish_json(topic: str, key: str, payload: dict[str, Any]) -> None:
    producer = _get_producer()
    producer.produce(topic=topic, key=key.encode("utf-8"), value=json.dumps(payload))
    producer.poll(0)


def _metric_payload(window: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in window.items() if key != "emitted"}
    payload["generatedAt"] = _utc_now_iso()
    return payload


def _append_verification_metric(event_time: datetime, status: str) -> None:
    minute_start = _floor_to_minute(event_time)
    with _lock:
        bucket_1m = _verification_metrics_1m.setdefault(
            _window_key(minute_start),
            _build_window(minute_start, duration_minutes=1),
        )
        bucket_1m["counts"][status] = bucket_1m["counts"].get(status, 0) + 1
        _recalculate_rates(bucket_1m)

        for offset in range(5):
            window_start = minute_start - timedelta(minutes=offset)
            bucket_5m = _verification_metrics_5m.setdefault(
                _window_key(window_start),
                _build_window(window_start, duration_minutes=5, hop_size_seconds=60),
            )
            bucket_5m["counts"][status] = bucket_5m["counts"].get(status, 0) + 1
            _recalculate_rates(bucket_5m)


def _open_sla_case(event: dict[str, Any]) -> None:
    content_id = event["contentId"]
    report_id = event["reportId"]
    opened_at = event["eventTime"]
    with _lock:
        existing = _open_sla_cases.get(content_id)
        if existing and existing["reportId"] == report_id:
            return
        _open_sla_cases[content_id] = {
            "contentId": content_id,
            "reportId": report_id,
            "status": "open",
            "openedAt": _isoformat(opened_at),
            "lastEventTime": _isoformat(opened_at),
            "alertPublished": False,
            "userId": event.get("userId"),
        }
        pending_outcome = _recent_sla_outcomes.get(content_id)
        if pending_outcome and pending_outcome["reportId"] == report_id:
            if pending_outcome["eventTime"] >= opened_at:
                _close_sla_case_locked(pending_outcome)


def _close_sla_case_locked(event: dict[str, Any]) -> None:
    content_id = event["contentId"]
    closed_at = event["eventTime"]
    case = _open_sla_cases.get(content_id)
    if case is None:
        return

    opened_at = _parse_event_time(case["openedAt"])
    age_seconds = int((closed_at - opened_at).total_seconds())
    case["closedAt"] = _isoformat(closed_at)
    case["lastEventTime"] = _isoformat(closed_at)
    case["resolvedBy"] = event["status"]
    case["currentAgeSeconds"] = max(age_seconds, 0)
    case["withinSla"] = age_seconds <= SLA_SECONDS

    violation = _sla_violations.get(content_id)
    if violation is not None:
        violation["status"] = "resolved-after-breach"
        violation["resolvedAt"] = _isoformat(closed_at)
        violation["resolvedBy"] = event["status"]
        violation["currentAgeSeconds"] = max(age_seconds, 0)

    _open_sla_cases.pop(content_id, None)
    _recent_sla_outcomes.pop(content_id, None)


def _close_sla_case(event: dict[str, Any]) -> None:
    content_id = event["contentId"]
    with _lock:
        _recent_sla_outcomes[content_id] = dict(event)
        _close_sla_case_locked(event)


def _normalize_verification_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    status = payload.get("status") or (payload.get("payload") or {}).get("status")
    content_id = payload.get("contentId") or (payload.get("payload") or {}).get("contentId")
    verification_id = (payload.get("payload") or {}).get("verificationId")
    if status not in VERIFICATION_STATUSES or not content_id or not verification_id:
        return None
    return {
        "topic": "verification-notification",
        "eventTime": _parse_event_time(payload.get("eventTime")),
        "status": status,
        "contentId": content_id,
        "verificationId": verification_id,
        "userId": payload.get("userId"),
    }


def _normalize_report_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    status = payload.get("status") or payload.get("type")
    body = payload.get("payload") or {}
    report_id = body.get("reportId") or payload.get("reportId")
    content_id = payload.get("contentId") or body.get("contentId") or body.get("postId")
    if not status or not report_id or not content_id:
        return None
    return {
        "topic": "report-notification",
        "eventTime": _parse_event_time(payload.get("eventTime")),
        "status": status,
        "reportId": report_id,
        "contentId": content_id,
        "userId": payload.get("userId"),
    }


def _normalize_outcome_event(topic: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    status = payload.get("status") or topic
    report_id = payload.get("reportId")
    content_id = payload.get("contentId") or payload.get("postId")
    if status not in SLA_OUTCOME_STATUSES or not report_id or not content_id:
        return None
    return {
        "topic": topic,
        "eventTime": _parse_event_time(payload.get("eventTime")),
        "status": status,
        "reportId": report_id,
        "contentId": content_id,
        "userId": payload.get("postOwnerId"),
    }


def _process_event(topic: str, payload: dict[str, Any]) -> None:
    if topic == "verification-notification":
        event = _normalize_verification_event(payload)
        if event is None:
            return
        _append_verification_metric(event["eventTime"], event["status"])
        return

    if topic == "report-notification":
        event = _normalize_report_event(payload)
        if event is None:
            return
        if event["status"] == "report-accepted":
            _open_sla_case(event)
        return

    if topic in {"post-deleted", "objection-approved"}:
        event = _normalize_outcome_event(topic, payload)
        if event is None:
            return
        _close_sla_case(event)


def _publish_closed_windows() -> None:
    now = _utc_now()
    grace_deadline = now - timedelta(seconds=WINDOW_GRACE_SECONDS)
    retained_after = now - timedelta(minutes=WINDOW_RETENTION_MINUTES)

    to_publish: list[tuple[str, str, dict[str, Any]]] = []

    with _lock:
        for topic, store in (
            (VERIFICATION_METRICS_1M_TOPIC, _verification_metrics_1m),
            (VERIFICATION_METRICS_5M_HOP_TOPIC, _verification_metrics_5m),
        ):
            for window_key, window in list(store.items()):
                window_end = _parse_event_time(window["windowEnd"])
                if not window["emitted"] and window_end <= grace_deadline:
                    window["emitted"] = True
                    to_publish.append((topic, window_key, _metric_payload(window)))
                if window_end <= retained_after:
                    store.pop(window_key, None)

    for topic, key, payload in to_publish:
        _publish_json(topic, key, payload)
        log.info("[publish-metric] topic=%s key=%s total=%s", topic, key, payload["total"])


def _check_sla_breaches() -> None:
    now = _utc_now()
    pending_alerts: list[tuple[str, dict[str, Any]]] = []

    with _lock:
        for content_id, case in _open_sla_cases.items():
            if case["alertPublished"]:
                continue

            opened_at = _parse_event_time(case["openedAt"])
            age_seconds = int((now - opened_at).total_seconds())
            if age_seconds <= SLA_SECONDS:
                continue

            case["alertPublished"] = True
            case["status"] = "open-breach"
            case["currentAgeSeconds"] = age_seconds

            payload = {
                "contentId": content_id,
                "reportId": case["reportId"],
                "openedAt": case["openedAt"],
                "breachedAt": _utc_now_iso(),
                "slaSeconds": SLA_SECONDS,
                "currentAgeSeconds": age_seconds,
                "status": "open-breach",
                "reason": "No post-deleted or objection-approved outcome within SLA",
            }
            _sla_violations[content_id] = dict(payload)
            pending_alerts.append((content_id, payload))

    for content_id, payload in pending_alerts:
        _publish_json(SLA_VIOLATIONS_TOPIC, content_id, payload)
        log.warning(
            "[sla-breach] contentId=%s reportId=%s ageSeconds=%s",
            content_id,
            payload["reportId"],
            payload["currentAgeSeconds"],
        )


def _monitor_loop() -> None:
    while True:
        try:
            _publish_closed_windows()
            _check_sla_breaches()
        except Exception:
            log.exception("monitor loop failed")
        time.sleep(SCAN_INTERVAL_SECONDS)


def _consumer_loop() -> None:
    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": KAFKA_GROUP_ID,
            "client.id": "sla-monitor",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe(INPUT_TOPICS)
    _consumer_ready.set()
    log.info("Kafka consumer started, subscribed to %s on %s", INPUT_TOPICS, KAFKA_BOOTSTRAP_SERVERS)

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
                _process_event(topic, payload)
            except Exception:
                log.exception("Failed to process Kafka message topic=%s", topic)
            finally:
                consumer.commit(message=msg)
    finally:
        consumer.close()


def _latest_window(store: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not store:
        return None
    latest_key = max(store)
    return dict(store[latest_key])


def _recent_windows(store: dict[str, dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    windows = [dict(store[key]) for key in sorted(store.keys(), reverse=True)[:limit]]
    return windows


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "sla-monitor",
            "consumerReady": _consumer_ready.is_set(),
            "slaSeconds": SLA_SECONDS,
            "windowGraceSeconds": WINDOW_GRACE_SECONDS,
        }
    )


@app.get("/metrics/verification")
def get_verification_metrics():
    window = request.args.get("window")
    limit = max(1, min(int(request.args.get("limit", "10")), 100))

    with _lock:
        if window == "1m":
            latest = _latest_window(_verification_metrics_1m)
            recent = _recent_windows(_verification_metrics_1m, limit)
            return jsonify({"window": "1m", "latest": latest, "windows": recent})
        if window == "5m":
            latest = _latest_window(_verification_metrics_5m)
            recent = _recent_windows(_verification_metrics_5m, limit)
            return jsonify({"window": "5m", "latest": latest, "windows": recent})
        return jsonify(
            {
                "latest1m": _latest_window(_verification_metrics_1m),
                "latest5m": _latest_window(_verification_metrics_5m),
            }
        )


@app.get("/sla/open-cases")
def get_open_sla_cases():
    with _lock:
        cases = [dict(case) for case in _open_sla_cases.values()]
    cases.sort(key=lambda item: item["openedAt"], reverse=True)
    return jsonify({"count": len(cases), "cases": cases})


@app.get("/sla/violations")
def get_sla_violations():
    include_resolved = request.args.get("include_resolved", "false").lower() in {"1", "true", "yes"}
    with _lock:
        violations = [dict(item) for item in _sla_violations.values()]
    if not include_resolved:
        violations = [item for item in violations if item.get("status") == "open-breach"]
    violations.sort(key=lambda item: item["openedAt"], reverse=True)
    return jsonify({"count": len(violations), "violations": violations})


threading.Thread(target=_consumer_loop, daemon=True, name="sla-monitor-consumer").start()
threading.Thread(target=_monitor_loop, daemon=True, name="sla-monitor-monitor").start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
