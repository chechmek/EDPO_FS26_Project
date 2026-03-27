"""
Reporting Service
=================
Orchestrates the ReportContent.bpmn process via Camunda Zeebe.
Post owners can submit objections which are correlated back to the waiting instance.

Zeebe job types owned by this service:
  - send-report-notification  → "Update report" service task
  - delete-post               → "Delete Post" service task

Camunda message names (used for correlation):
  - post-owner-objection      → "Objection" catch event in ReportContent.bpmn

REST endpoints:
  POST /reports                        → submit a content report (starts Camunda process)
  GET  /reports/<id>                   → get report status
  POST /reports/<id>/objection         → post owner objects to the report
  GET  /health

Objection simulation:
  After a valid report notification is sent, there is a 20% chance that a simulated
  objection is fired within 20 seconds (random delay). This stands in for a real
  post owner receiving a notification and responding via the API.
"""

import asyncio
import logging
import os
import random
import threading
import uuid

from flask import Flask, jsonify, request
from pyzeebe import ZeebeClient, ZeebeWorker, create_insecure_channel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("reporting-service")

app = Flask(__name__)

ZEEBE_ADDRESS = os.getenv("ZEEBE_ADDRESS", "zeebe:26500")
REPORT_CONTENT_PROCESS_ID = "Process_0rsygf3"  # id from ReportContent.bpmn

# In-memory store: reportId → { reporterId, postId, postOwnerId, status, processInstanceKey }
_reports: dict[str, dict] = {}

# Shared event loop owned by the worker thread — request handlers submit to it
_loop: asyncio.AbstractEventLoop | None = None
_loop_ready = threading.Event()


def _zeebe_call(coro):
    """Submit a coroutine to the shared worker loop and block until done."""
    _loop_ready.wait()
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "reporting-service"})


@app.post("/reports")
def submit_report():
    """
    Starts the ReportContent Camunda process.
    Body: { "reporterId": "...", "postId": "...", "postOwnerId": "...", "reason": "..." }
    """
    body = request.get_json(force=True)
    reporter_id = body.get("reporterId")
    post_id = body.get("postId")
    post_owner_id = body.get("postOwnerId")
    if not reporter_id or not post_id or not post_owner_id:
        return jsonify({"error": "reporterId, postId, and postOwnerId required"}), 400

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

    _reports[report_id] = {
        "reporterId": reporter_id,
        "postId": post_id,
        "postOwnerId": post_owner_id,
        "status": "pending-review",
        "processInstanceKey": key,
    }

    return jsonify({"reportId": report_id, "processInstanceKey": key}), 202


@app.get("/reports/<report_id>")
def get_report(report_id):
    record = _reports.get(report_id)
    if not record:
        return jsonify({"error": "not found"}), 404
    return jsonify({"reportId": report_id, **record})


@app.post("/reports/<report_id>/objection")
def submit_objection(report_id):
    """
    Called by the post owner to object before the deadline.
    Correlates the 'post-owner-objection' message to the waiting Camunda instance.
    Body: { "explanation": "..." }
    """
    record = _reports.get(report_id)
    if not record:
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

    record["status"] = "objection-received"
    return jsonify({"received": True}), 200


# ---------------------------------------------------------------------------
# Zeebe workers
# ---------------------------------------------------------------------------

async def _simulate_objection(report_id: str, delay: float):
    """
    Fires a simulated post-owner-objection after `delay` seconds.
    Represents a post owner responding to a moderation notification.
    """
    await asyncio.sleep(delay)
    try:
        channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
        client = ZeebeClient(channel)
        await client.publish_message(
            name="post-owner-objection",
            correlation_key=report_id,
            variables={"objectionExplanation": "Simulated objection"},
        )
        await channel.close()
        log.info("[objection-simulation] fired for reportId=%s after %.1fs", report_id, delay)
        if report_id in _reports:
            _reports[report_id]["status"] = "objection-received"
    except Exception:
        log.exception("[objection-simulation] failed for reportId=%s", report_id)


async def _run_workers():
    global _loop
    _loop = asyncio.get_running_loop()
    _loop_ready.set()

    channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
    worker = ZeebeWorker(channel)

    @worker.task(task_type="send-report-notification")
    async def handle_send_notification(reportId: str, postId: str, postOwnerId: str, report_valid: bool = False, **kwargs) -> dict:
        """
        Fires after the moderator user task completes. Updates the report and,
        if the report was marked valid, schedules a 20% chance simulated objection
        within 20 seconds (simulating the post owner receiving the notification and responding).
        """
        log.info("[send-report-notification] reportId=%s postId=%s postOwnerId=%s valid=%s",
                 reportId, postId, postOwnerId, report_valid)

        if reportId in _reports:
            _reports[reportId]["status"] = "notification-sent" if report_valid else "dismissed"

        if report_valid and random.random() < 0.20:
            delay = random.uniform(0, 20)
            log.info("[send-report-notification] scheduling simulated objection in %.1fs for reportId=%s",
                     delay, reportId)
            asyncio.create_task(_simulate_objection(reportId, delay))

        return {"notificationSent": True}

    @worker.task(task_type="delete-post")
    async def handle_delete_post(postId: str, reportId: str, **kwargs) -> dict:
        """
        Deletes the reported post after the deadline passes with no objection,
        or after the moderator rejects the objection.
        """
        log.info("[delete-post] postId=%s reportId=%s", postId, reportId)
        # TODO: call post/content service to remove the post
        if reportId in _reports:
            _reports[reportId]["status"] = "deleted"
        return {"deleted": True}

    log.info("Zeebe workers started, connecting to %s", ZEEBE_ADDRESS)
    await worker.work()


def _start_worker_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run_workers())


threading.Thread(target=_start_worker_thread, daemon=True, name="zeebe-workers").start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8003)
