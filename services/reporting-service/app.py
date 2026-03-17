"""
Reporting Service
=================
Orchestrates the ReportContent.bpmn process via Camunda Zeebe.
Post owners can submit objections which are correlated back to the waiting instance.

Zeebe job types owned by this service:
  - send-report-notification  → "Send notification to post owner" service task
  - delete-post               → "Delete Post" service task

Camunda message names (used for correlation):
  - post-owner-objection      → "Objection" catch event in ReportContent.bpmn

REST endpoints:
  POST /reports                        → submit a content report (starts Camunda process)
  GET  /reports/<id>                   → get report status
  POST /reports/<id>/objection         → post owner objects to the report
  GET  /health
"""

import asyncio
import logging
import os
import threading
import uuid

from flask import Flask, jsonify, request
from pyzeebe import ZeebeClient, ZeebeWorker, create_insecure_channel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("reporting-service")

app = Flask(__name__)

ZEEBE_ADDRESS = os.getenv("ZEEBE_ADDRESS", "zeebe:26500")
REPORT_CONTENT_PROCESS_ID = "Process_0rsygf3"  # id from ReportContent.bpmn

# In-memory store — replace with a real DB
# key: report_id → { "reporterId", "contentId", "status", "processInstanceKey" }
_reports: dict[str, dict] = {}


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
    Body: { "reporterId": "...", "contentId": "...", "reason": "..." }
    """
    body = request.get_json(force=True)
    reporter_id = body.get("reporterId")
    content_id = body.get("contentId")
    if not reporter_id or not content_id:
        return jsonify({"error": "reporterId and contentId required"}), 400

    report_id = str(uuid.uuid4())
    variables = {
        "reportId": report_id,
        "reporterId": reporter_id,
        "contentId": content_id,
        "reason": body.get("reason", ""),
    }

    async def _start():
        channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
        client = ZeebeClient(channel)
        instance = await client.run_process(REPORT_CONTENT_PROCESS_ID, variables=variables)
        await channel.close()
        return instance.process_instance_key

    loop = asyncio.new_event_loop()
    key = loop.run_until_complete(_start())
    loop.close()

    _reports[report_id] = {
        "reporterId": reporter_id,
        "contentId": content_id,
        "status": "under-review",
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
    Called by the post owner to object to a report before the deadline.
    Correlates the 'post-owner-objection' message to the waiting Camunda instance.
    Body: { "ownerId": "...", "explanation": "..." }
    """
    record = _reports.get(report_id)
    if not record:
        return jsonify({"error": "not found"}), 404

    body = request.get_json(force=True)
    variables = {
        "objectionOwnerId": body.get("ownerId"),
        "objectionExplanation": body.get("explanation", ""),
    }

    async def _pub():
        channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
        client = ZeebeClient(channel)
        await client.publish_message(
            name="post-owner-objection",
            correlation_key=report_id,
            variables=variables,
        )
        await channel.close()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_pub())
    loop.close()

    record["status"] = "objection-received"
    return jsonify({"received": True}), 200


# ---------------------------------------------------------------------------
# Zeebe workers
# ---------------------------------------------------------------------------

async def _run_workers():
    channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
    worker = ZeebeWorker(channel)

    @worker.task(task_type="send-report-notification")
    async def handle_send_notification(reportId: str, contentId: str, **kwargs) -> dict:
        """
        Notifies the post owner that their content has been reported.
        They have until the deadline to object.
        Input variables:  reportId, contentId
        """
        log.info("[send-report-notification] reportId=%s contentId=%s", reportId, contentId)
        # TODO: look up post owner from content store / user-service
        # TODO: send email / push notification with objection link
        return {"notificationSent": True}

    @worker.task(task_type="delete-post")
    async def handle_delete_post(contentId: str, reportId: str, **kwargs) -> dict:
        """
        Deletes the reported content after the deadline with no objection.
        Input variables: contentId, reportId
        """
        log.info("[delete-post] contentId=%s reportId=%s", contentId, reportId)
        # TODO: call content store to delete / mark content as removed
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
