"""
Reporting Service
=================
Orchestrates the ReportContent.bpmn process via Camunda Zeebe.
Post owners can submit objections which are correlated back to the waiting instance.
"""

import asyncio
import json
import logging
import os
import threading
import uuid
from typing import Any

from confluent_kafka import Producer
from flask import Flask, jsonify, request
from pyzeebe import ZeebeClient, ZeebeWorker, create_insecure_channel
import requests as http

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("reporting-service")

app = Flask(__name__)

ZEEBE_ADDRESS = os.getenv("ZEEBE_ADDRESS", "zeebe:26500")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
ATTESTATION_SERVICE_URL = os.getenv("ATTESTATION_SERVICE_URL", "http://attestation-service:8004")
REPORT_CONTENT_PROCESS_ID = "Process_0rsygf3"

_reports: dict[str, dict[str, Any]] = {}
_posts: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

_loop: asyncio.AbstractEventLoop | None = None
_loop_ready = threading.Event()
_producer: Producer | None = None
_producer_lock = threading.Lock()

_OBJECTION_MODES = {"manual", "auto-object", "auto-silent"}


def _zeebe_call(coro):
    """Submit a coroutine to the shared worker loop and block until done."""
    _loop_ready.wait()
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()


def _coerce_float(value: Any, default: float, *, minimum: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, number)


def _get_producer() -> Producer:
    global _producer
    with _producer_lock:
        if _producer is None:
            _producer = Producer(
                {
                    "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
                    "client.id": "reporting-service",
                    "acks": "all",
                    "enable.idempotence": True,
                }
            )
    return _producer


def _publish_event(topic: str, key: str, payload: dict[str, Any]) -> None:
    producer = _get_producer()
    producer.produce(topic=topic, key=key.encode("utf-8"), value=json.dumps(payload))
    producer.flush(5.0)


def _get_report(report_id: str) -> dict[str, Any] | None:
    with _lock:
        record = _reports.get(report_id)
        return None if record is None else dict(record)


def _update_report(report_id: str, **fields: Any) -> dict[str, Any] | None:
    with _lock:
        record = _reports.get(report_id)
        if record is None:
            return None
        record.update(fields)
        return dict(record)


def _send_notification(user_id: str, notif_type: str, message: str, payload: dict[str, Any]) -> None:
    _publish_event(
        "report-notification",
        payload.get("reportId", user_id),
        {
            "userId": user_id,
            "type": notif_type,
            "message": message,
            "payload": payload,
        },
    )


def _invalidate_signature_for_post(post_id: str, signature_id: str | None, report_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "invalidatedBy": "reporting-service",
        "reason": f"Post {post_id} deleted by report {report_id}",
    }
    if signature_id:
        payload["signatureId"] = signature_id
    else:
        payload["contentUrl"] = post_id

    try:
        resp = http.post(f"{ATTESTATION_SERVICE_URL}/attestations/invalidate", json=payload, timeout=5)
        if resp.status_code == 404:
            return {"attempted": True, "invalidated": 0, "attestationFound": False}
        resp.raise_for_status()
        body = resp.json()
        return {"attempted": True, "invalidated": body.get("invalidated", 0), "attestationFound": True}
    except Exception as exc:
        log.warning("[delete-post] failed to invalidate signature for postId=%s: %s", post_id, exc)
        return {"attempted": False, "invalidated": 0, "attestationFound": False}


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "reporting-service"})


@app.post("/reports")
def submit_report():
    """
    Starts the ReportContent Camunda process.
    Body:
      {
        "reporterId": "...",
        "postId": "...",
        "postOwnerId": "...",
        "signatureId": "...",
        "reason": "...",
        "objectionMode": "manual|auto-object|auto-silent",
        "objectionDelaySeconds": 5
      }
    """
    body = request.get_json(force=True)
    reporter_id = body.get("reporterId")
    post_id = body.get("postId")
    post_owner_id = body.get("postOwnerId")
    signature_id = body.get("signatureId")
    if not reporter_id or not post_id or not post_owner_id:
        return jsonify({"error": "reporterId, postId, and postOwnerId required"}), 400

    objection_mode = body.get("objectionMode", "manual")
    if objection_mode not in _OBJECTION_MODES:
        return jsonify({"error": f"objectionMode must be one of {sorted(_OBJECTION_MODES)}"}), 400

    report_id = str(uuid.uuid4())
    variables = {
        "reportId": report_id,
        "reporterId": reporter_id,
        "postId": post_id,
        "postOwnerId": post_owner_id,
        "reason": body.get("reason", ""),
    }

    async def _start():
        channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
        client = ZeebeClient(channel)
        instance = await client.run_process(REPORT_CONTENT_PROCESS_ID, variables=variables)
        await channel.close()
        return instance.process_instance_key

    key = _zeebe_call(_start())

    with _lock:
        _reports[report_id] = {
            "reportId": report_id,
            "reporterId": reporter_id,
            "postId": post_id,
            "postOwnerId": post_owner_id,
            "signatureId": signature_id,
            "reason": body.get("reason", ""),
            "status": "pending-review",
            "processInstanceKey": key,
            "objectionMode": objection_mode,
            "objectionDelaySeconds": _coerce_float(body.get("objectionDelaySeconds"), 5.0),
        }
        _posts.setdefault(
            post_id,
            {
                "postId": post_id,
                "postOwnerId": post_owner_id,
                "signatureId": signature_id,
                "deleted": False,
                "lastReportId": report_id,
            },
        )

    return jsonify({"reportId": report_id, "processInstanceKey": key}), 202


@app.get("/reports/<report_id>")
def get_report(report_id):
    record = _get_report(report_id)
    if record is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(record)


@app.post("/reports/<report_id>/objection")
def submit_objection(report_id):
    """
    Called by the post owner to object before the deadline.
    Correlates the 'post-owner-objection' message to the waiting Camunda instance.
    Body: { "explanation": "..." }
    """
    record = _get_report(report_id)
    if record is None:
        return jsonify({"error": "not found"}), 404

    body = request.get_json(force=True)

    async def _pub():
        channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
        client = ZeebeClient(channel)
        await client.publish_message(
            name="post-owner-objection",
            correlation_key=report_id,
            variables={"objectionExplanation": body.get("explanation", "")},
        )
        await channel.close()

    _zeebe_call(_pub())
    _update_report(report_id, status="objection-received")
    return jsonify({"received": True}), 200


async def _simulate_objection(report_id: str, delay: float):
    await asyncio.sleep(delay)
    try:
        channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
        client = ZeebeClient(channel)
        await client.publish_message(
            name="post-owner-objection",
            correlation_key=report_id,
            variables={"objectionExplanation": "Automatically submitted objection"},
        )
        await channel.close()
        _update_report(report_id, status="objection-received")
        log.info("[objection-simulation] fired for reportId=%s after %.1fs", report_id, delay)
    except Exception:
        log.exception("[objection-simulation] failed for reportId=%s", report_id)


async def _run_workers():
    global _loop
    _loop = asyncio.get_running_loop()
    _loop_ready.set()

    channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
    worker = ZeebeWorker(channel)

    @worker.task(task_type="send-report-notification")
    async def handle_send_notification(
        reportId: str,
        reporterId: str,
        postId: str,
        postOwnerId: str,
        report_valid: bool = False,
        **kwargs,
    ) -> dict:
        """
        Updates local state and sends in-memory notifications to the involved users.
        """
        log.info(
            "[send-report-notification] reportId=%s postId=%s postOwnerId=%s valid=%s",
            reportId,
            postId,
            postOwnerId,
            report_valid,
        )

        record = _get_report(reportId)
        if record is None:
            return {"notificationSent": False}

        if report_valid:
            _update_report(reportId, status="awaiting-objection")
            _send_notification(
                postOwnerId,
                "report-valid",
                "A moderator marked a report against your post as valid. You can object within 15 seconds.",
                {"reportId": reportId, "postId": postId},
            )
            _send_notification(
                reporterId,
                "report-accepted",
                "Your report was accepted and the post owner has been notified.",
                {"reportId": reportId, "postId": postId},
            )

            if record["objectionMode"] == "auto-object":
                asyncio.create_task(_simulate_objection(reportId, record["objectionDelaySeconds"]))
        else:
            _update_report(reportId, status="dismissed")
            _send_notification(
                reporterId,
                "report-dismissed",
                "Your report was dismissed by moderation.",
                {"reportId": reportId, "postId": postId},
            )

        return {"notificationSent": True}

    @worker.task(task_type="delete-post")
    async def handle_delete_post(postId: str, reportId: str, **kwargs) -> dict:
        """
        Deletes the reported post in the in-memory post store.
        """
        log.info("[delete-post] postId=%s reportId=%s", postId, reportId)
        signature_id: str | None = None
        with _lock:
            post_record = _posts.setdefault(postId, {"postId": postId, "deleted": False})
            post_record["deleted"] = True
            post_record["lastReportId"] = reportId
            signature_id = post_record.get("signatureId")
            if reportId in _reports:
                _reports[reportId]["status"] = "deleted"
                signature_id = signature_id or _reports[reportId].get("signatureId")

        invalidation = _invalidate_signature_for_post(postId, signature_id, reportId)
        event_payload = {
            "reportId": reportId,
            "postId": postId,
            "postOwnerId": kwargs.get("postOwnerId"),
            "signatureId": signature_id,
            "signatureInvalidated": invalidation.get("invalidated", 0) > 0,
            "signatureInvalidatedCount": invalidation.get("invalidated", 0),
        }
        _publish_event("post-deleted", postId, event_payload)
        log.info(
            "[delete-post] published post-deleted reportId=%s postId=%s invalidated=%s",
            reportId,
            postId,
            invalidation.get("invalidated", 0),
        )
        return {
            "deleted": True,
            "signatureInvalidated": invalidation.get("invalidated", 0) > 0,
            "signatureInvalidatedCount": invalidation.get("invalidated", 0),
            "postDeletedEventPublished": True,
        }

    @worker.task(task_type="objection-approved")
    async def handle_objection_approved(reportId: str, postId: str, postOwnerId: str, **kwargs) -> dict:
        payload = {"reportId": reportId, "postId": postId, "postOwnerId": postOwnerId}
        _publish_event("objection-approved", reportId, payload)
        log.info("[objection-approved] reportId=%s postId=%s", reportId, postId)
        return {}

    @worker.task(task_type="publish-objection-approved")
    async def handle_publish_objection_approved_legacy(reportId: str, postId: str, postOwnerId: str, **kwargs) -> dict:
        payload = {"reportId": reportId, "postId": postId, "postOwnerId": postOwnerId}
        _publish_event("objection-approved", reportId, payload)
        log.info("[publish-objection-approved] reportId=%s postId=%s", reportId, postId)
        return {}

    @worker.task(task_type="publish-post-deleted")
    async def handle_publish_post_deleted(
        reportId: str,
        postId: str,
        postOwnerId: str,
        postDeletedEventPublished: bool = False,
        **kwargs,
    ) -> dict:
        if postDeletedEventPublished:
            log.info("[publish-post-deleted] skipped; already published by delete-post reportId=%s postId=%s", reportId, postId)
            return {}
        payload = {"reportId": reportId, "postId": postId, "postOwnerId": postOwnerId}
        _publish_event("post-deleted", postId, payload)
        log.info("[publish-post-deleted] reportId=%s postId=%s", reportId, postId)
        return {}

    log.info("Zeebe workers started, connecting to %s", ZEEBE_ADDRESS)
    await worker.work()


def _start_worker_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run_workers())


threading.Thread(target=_start_worker_thread, daemon=True, name="zeebe-workers").start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8003)
