"""
Verification Service
====================
Orchestrates the VerifyContent.bpmn process via Camunda Zeebe.
Receives peer verdicts and correlates them back to waiting process instances.

Zeebe job types owned by this service:
  - check-user-registration      → calls user-service to validate requester
  - send-verification-request    → dispatches verification request to peers
  - internal-verification        → runs internal checks after peer phase
  - publish-signature            → generates and publishes the content signature
  - send-verification-notification → notifies the requesting user of the outcome

Camunda message names (used for correlation):
  - peer-approved                → "All peer approvals received" catch event
  - peer-rejected                → "Peer rejection received" catch event

REST endpoints:
  POST /verifications                              → start VerifyContent process
  GET  /verifications/<id>                         → get verification record
  POST /verifications/<id>/peer-response           → receive verdict from a peer
  GET  /health
"""

import asyncio
import logging
import os
import threading
import uuid

import requests as http
from flask import Flask, jsonify, request
from pyzeebe import ZeebeClient, ZeebeWorker, create_insecure_channel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("verification-service")

app = Flask(__name__)

ZEEBE_ADDRESS = os.getenv("ZEEBE_ADDRESS", "zeebe:26500")
USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://user-service:8001")
VERIFY_CONTENT_PROCESS_ID = "Process_01gn4xr"  # id from VerifyContent.bpmn

# In-memory store — replace with a real DB
# key: verification_id → { "userId", "contentUrl", "status", "processInstanceKey", "peerVerdicts": [] }
_verifications: dict[str, dict] = {}

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
    return jsonify({"status": "ok", "service": "verification-service"})


@app.post("/verifications")
def start_verification():
    """
    Starts the VerifyContent Camunda process.
    Body: { "userId": "...", "contentUrl": "...", "contentTitle": "..." }
    """
    body = request.get_json(force=True)
    user_id = body.get("userId")
    content_url = body.get("contentUrl")
    if not user_id or not content_url:
        return jsonify({"error": "userId and contentUrl required"}), 400

    verification_id = str(uuid.uuid4())
    variables = {
        "verificationId": verification_id,
        "userId": user_id,
        "contentUrl": content_url,
        "contentTitle": body.get("contentTitle", ""),
    }

    async def _start():
        channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
        client = ZeebeClient(channel)
        instance = await client.run_process(VERIFY_CONTENT_PROCESS_ID, variables=variables)
        await channel.close()
        return instance.process_instance_key

    key = _zeebe_call(_start())

    _verifications[verification_id] = {
        "userId": user_id,
        "contentUrl": content_url,
        "status": "pending",
        "processInstanceKey": key,
        "peerVerdicts": [],
    }

    return jsonify({"verificationId": verification_id, "processInstanceKey": key}), 202


@app.get("/verifications/<verification_id>")
def get_verification(verification_id):
    record = _verifications.get(verification_id)
    if not record:
        return jsonify({"error": "not found"}), 404
    return jsonify({"verificationId": verification_id, **record})


@app.post("/verifications/<verification_id>/peer-response")
def receive_peer_response(verification_id):
    """
    Called by external peers to submit their verdict.
    Body: { "peerId": "...", "approved": true/false, "reason": "..." }

    This endpoint aggregates verdicts and, once a decision threshold is met,
    publishes a Camunda message to unblock the waiting process instance.
    """
    record = _verifications.get(verification_id)
    if not record:
        return jsonify({"error": "not found"}), 404

    body = request.get_json(force=True)
    peer_id = body.get("peerId")
    approved = body.get("approved", False)

    record["peerVerdicts"].append({"peerId": peer_id, "approved": approved})

    # TODO: implement real quorum logic (e.g. majority of N peers)
    # For now: any rejection → reject; all approve → approve
    verdicts = record["peerVerdicts"]
    if not approved:
        _publish_camunda_message("peer-rejected", verification_id, {"peerId": peer_id})
        record["status"] = "rejected"
    elif len(verdicts) >= 1:  # TODO: replace 1 with required peer count
        _publish_camunda_message("peer-approved", verification_id, {"verdicts": verdicts})
        record["status"] = "peer-approved"

    return jsonify({"received": True}), 200


def _publish_camunda_message(name: str, correlation_key: str, variables: dict):
    async def _pub():
        channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
        client = ZeebeClient(channel)
        await client.publish_message(
            name=name,
            correlation_key=correlation_key,
            variables=variables,
        )
        await channel.close()

    _zeebe_call(_pub())


# ---------------------------------------------------------------------------
# Zeebe workers
# ---------------------------------------------------------------------------

async def _run_workers():
    global _loop
    _loop = asyncio.get_running_loop()
    _loop_ready.set()

    channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
    worker = ZeebeWorker(channel)

    @worker.task(task_type="check-user-registration")
    async def handle_check_registration(userId: str, **kwargs) -> dict:
        """
        Calls user-service to verify the requester is a registered user.
        Output variables: userRegistered (bool)
        """
        log.info("[check-user-registration] userId=%s", userId)
        try:
            resp = http.get(f"{USER_SERVICE_URL}/users/{userId}", timeout=5)
            registered = resp.status_code == 200 and resp.json().get("registered", False)
        except Exception as exc:
            log.warning("[check-user-registration] user-service unreachable: %s", exc)
            registered = False
        return {"userRegistered": registered}

    @worker.task(task_type="send-verification-request")
    async def handle_send_request(verificationId: str, contentUrl: str, **kwargs) -> dict:
        """
        Sends the content to registered peers for verification.
        In production: publish to Kafka topic / call peer API.
        Output variables: peersSent (int)
        """
        log.info("[send-verification-request] verificationId=%s url=%s", verificationId, contentUrl)
        # TODO: publish VerificationRequested event to Kafka
        # TODO: record which peers were contacted
        return {"peersSent": 3}  # stub — replace with actual peer count

    @worker.task(task_type="internal-verification")
    async def handle_internal_verification(verificationId: str, **kwargs) -> dict:
        """
        Runs internal content checks after the peer phase.
        Called on both 'all approvals received' and 'timeout' paths.
        Output variables: internalCheckPassed (bool)
        """
        log.info("[internal-verification] verificationId=%s", verificationId)
        # TODO: run internal heuristics / ML model / hash check
        return {"internalCheckPassed": True}

    @worker.task(task_type="publish-signature")
    async def handle_publish_signature(verificationId: str, contentUrl: str, **kwargs) -> dict:
        """
        Generates and publishes a cryptographic signature for the verified content.
        Output variables: signatureId, signatureHash
        """
        log.info("[publish-signature] verificationId=%s", verificationId)
        signature_id = str(uuid.uuid4())
        # TODO: generate real signature (e.g. RSA/ECDSA over content hash)
        # TODO: publish to public ledger / Kafka topic
        if verificationId in _verifications:
            _verifications[verificationId]["status"] = "verified"
            _verifications[verificationId]["signatureId"] = signature_id
        return {"signatureId": signature_id, "signatureHash": "stub-hash"}

    @worker.task(task_type="send-verification-notification")
    async def handle_send_notification(userId: str, verificationId: str,
                                       internalCheckPassed: bool = False, **kwargs) -> dict:
        """
        Notifies the requesting user of the verification outcome.
        """
        outcome = "verified" if internalCheckPassed else "rejected"
        log.info("[send-verification-notification] userId=%s verificationId=%s outcome=%s",
                 userId, verificationId, outcome)
        # TODO: send email / push notification
        return {}

    log.info("Zeebe workers started, connecting to %s", ZEEBE_ADDRESS)
    await worker.work()


def _start_worker_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run_workers())


threading.Thread(target=_start_worker_thread, daemon=True, name="zeebe-workers").start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002)
