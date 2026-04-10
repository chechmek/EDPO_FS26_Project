"""
Verification Service
====================
Orchestrates the VerifyContent.bpmn process via Camunda Zeebe.
Receives peer verdicts and correlates them back to waiting process instances.
"""

import asyncio
import hashlib
import logging
import os
import threading
import uuid
from typing import Any

import requests as http
from flask import Flask, jsonify, request
from pyzeebe import ZeebeClient, ZeebeWorker, create_insecure_channel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("verification-service")

app = Flask(__name__)

ZEEBE_ADDRESS = os.getenv("ZEEBE_ADDRESS", "zeebe:26500")
USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://user-service:8001")
NOTIFICATION_SERVICE_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification-service:8005")
ATTESTATION_SERVICE_URL = os.getenv("ATTESTATION_SERVICE_URL", "http://attestation-service:8004")
VERIFY_CONTENT_PROCESS_ID = "Process_01gn4xr"

_verifications: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

_loop: asyncio.AbstractEventLoop | None = None
_loop_ready = threading.Event()

_FINAL_STATUSES = {
    "rejected-unregistered",
    "rejected-peer",
    "rejected-internal",
    "timed-out",
    "verified",
}
_PEER_MODES = {"manual", "auto-approve", "auto-reject", "auto-mixed"}


def _zeebe_call(coro):
    """Submit a coroutine to the shared worker loop and block until done."""
    _loop_ready.wait()
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _coerce_int(value: Any, default: int, *, minimum: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, number)


def _coerce_float(value: Any, default: float, *, minimum: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, number)


def _notification_enabled() -> bool:
    return bool(NOTIFICATION_SERVICE_URL)


def _get_verification(verification_id: str) -> dict[str, Any] | None:
    with _lock:
        record = _verifications.get(verification_id)
        return None if record is None else dict(record)


def _update_verification(verification_id: str, **fields: Any) -> dict[str, Any] | None:
    with _lock:
        record = _verifications.get(verification_id)
        if record is None:
            return None
        record.update(fields)
        return dict(record)


def _send_notification(user_id: str, notif_type: str, message: str, payload: dict[str, Any]) -> None:
    if not _notification_enabled():
        return
    try:
        resp = http.post(
            f"{NOTIFICATION_SERVICE_URL}/internal/notifications",
            json={
                "userId": user_id,
                "type": notif_type,
                "message": message,
                "payload": payload,
            },
            timeout=5,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.warning("[notification] failed to notify userId=%s: %s", user_id, exc)


async def _publish_camunda_message_async(name: str, correlation_key: str, variables: dict[str, Any]) -> None:
    channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
    client = ZeebeClient(channel)
    await client.publish_message(name=name, correlation_key=correlation_key, variables=variables)
    await channel.close()


def _publish_camunda_message(name: str, correlation_key: str, variables: dict[str, Any]) -> None:
    _zeebe_call(_publish_camunda_message_async(name, correlation_key, variables))


def _register_peer_verdict(
    verification_id: str,
    *,
    peer_id: str,
    approved: bool,
    reason: str,
    source: str,
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
    with _lock:
        record = _verifications.get(verification_id)
        if record is None:
            return None, None, None

        if record["status"] in _FINAL_STATUSES:
            return dict(record), None, None

        existing_peer_ids = {verdict["peerId"] for verdict in record["peerVerdicts"]}
        if peer_id in existing_peer_ids:
            return dict(record), None, None

        verdict = {
            "peerId": peer_id,
            "approved": approved,
            "reason": reason,
            "source": source,
        }
        record["peerVerdicts"].append(verdict)

        approved_count = sum(1 for item in record["peerVerdicts"] if item["approved"])
        has_rejection = any(not item["approved"] for item in record["peerVerdicts"])

        event_name = None
        event_variables = None

        if not record["camundaDecisionPublished"] and has_rejection:
            record["camundaDecisionPublished"] = True
            record["status"] = "rejected-peer"
            event_name = "peer-rejected"
            event_variables = {"peerId": peer_id, "reason": reason}
        elif not record["camundaDecisionPublished"] and approved_count >= record["requiredApprovalCount"]:
            record["camundaDecisionPublished"] = True
            record["status"] = "peer-approved"
            event_name = "peer-approved"
            event_variables = {
                "approvedCount": approved_count,
                "verdicts": list(record["peerVerdicts"]),
            }
        else:
            record["status"] = "waiting-for-peers"

        return dict(record), event_name, event_variables


async def _submit_peer_verdict_async(
    verification_id: str,
    *,
    peer_id: str,
    approved: bool,
    reason: str = "",
    source: str,
) -> dict[str, Any] | None:
    record, event_name, event_variables = _register_peer_verdict(
        verification_id,
        peer_id=peer_id,
        approved=approved,
        reason=reason,
        source=source,
    )
    if record is None:
        return None
    if event_name is not None and event_variables is not None:
        await _publish_camunda_message_async(event_name, verification_id, event_variables)
    return _get_verification(verification_id)


def _submit_peer_verdict_sync(
    verification_id: str,
    *,
    peer_id: str,
    approved: bool,
    reason: str = "",
    source: str,
) -> dict[str, Any] | None:
    return _zeebe_call(
        _submit_peer_verdict_async(
            verification_id,
            peer_id=peer_id,
            approved=approved,
            reason=reason,
            source=source,
        )
    )


async def _simulate_peer_responses(verification_id: str) -> None:
    record = _get_verification(verification_id)
    if record is None:
        return

    mode = record["peerMode"]
    if mode == "manual":
        return

    peer_ids = list(record["peerIds"])
    delay_seconds = record["peerResponseDelaySeconds"]
    required_approval_count = record["requiredApprovalCount"]

    if mode == "auto-reject":
        plan = [(peer_ids[0], False, "Automatically rejected by simulation")]
    elif mode == "auto-mixed":
        approvals = max(1, required_approval_count - 1)
        plan = [(peer_ids[index], True, "Automatically approved by simulation") for index in range(approvals)]
        reject_peer = peer_ids[min(approvals, len(peer_ids) - 1)]
        plan.append((reject_peer, False, "Automatically rejected by simulation"))
    else:
        approvals = min(required_approval_count, len(peer_ids))
        plan = [
            (peer_ids[index], True, "Automatically approved by simulation")
            for index in range(approvals)
        ]

    for index, (peer_id, approved, reason) in enumerate(plan, start=1):
        await asyncio.sleep(delay_seconds * index)
        latest = _get_verification(verification_id)
        if latest is None or latest["status"] in _FINAL_STATUSES:
            return
        await _submit_peer_verdict_async(
            verification_id,
            peer_id=peer_id,
            approved=approved,
            reason=reason,
            source="simulation",
        )


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "verification-service"})


@app.post("/verifications")
def start_verification():
    """
    Starts the VerifyContent Camunda process.
    Body:
      {
        "userId": "...",
        "contentUrl": "...",
        "contentTitle": "...",
        "requestedPeerCount": 3,
        "requiredApprovalCount": 2,
        "peerMode": "manual|auto-approve|auto-reject|auto-mixed",
        "peerResponseDelaySeconds": 1,
        "forceInternalFailure": false
      }
    """
    body = request.get_json(force=True)
    user_id = body.get("userId")
    content_url = body.get("contentUrl")
    if not user_id or not content_url:
        return jsonify({"error": "userId and contentUrl required"}), 400

    requested_peer_count = _coerce_int(body.get("requestedPeerCount"), 3)
    required_approval_count = _coerce_int(
        body.get("requiredApprovalCount"),
        requested_peer_count,
    )
    required_approval_count = min(required_approval_count, requested_peer_count)
    peer_mode = body.get("peerMode", "manual")
    if peer_mode not in _PEER_MODES:
        return jsonify({"error": f"peerMode must be one of {sorted(_PEER_MODES)}"}), 400

    verification_id = str(uuid.uuid4())
    peer_response_delay_seconds = _coerce_float(body.get("peerResponseDelaySeconds"), 1.0)
    force_internal_failure = _coerce_bool(body.get("forceInternalFailure"), False)

    variables = {
        "verificationId": verification_id,
        "userId": user_id,
        "contentUrl": content_url,
        "contentTitle": body.get("contentTitle", ""),
        "send_verify_retry": 0,
        "forceInternalFailure": force_internal_failure,
    }

    async def _start():
        channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
        client = ZeebeClient(channel)
        instance = await client.run_process(VERIFY_CONTENT_PROCESS_ID, variables=variables)
        await channel.close()
        return instance.process_instance_key

    key = _zeebe_call(_start())

    with _lock:
        _verifications[verification_id] = {
            "verificationId": verification_id,
            "userId": user_id,
            "contentUrl": content_url,
            "contentTitle": body.get("contentTitle", ""),
            "status": "pending",
            "processInstanceKey": key,
            "peerVerdicts": [],
            "peerIds": [],
            "requestedPeerCount": requested_peer_count,
            "requiredApprovalCount": required_approval_count,
            "peerMode": peer_mode,
            "peerResponseDelaySeconds": peer_response_delay_seconds,
            "forceInternalFailure": force_internal_failure,
            "camundaDecisionPublished": False,
            "signatureId": None,
            "signatureHash": None,
        }

    return jsonify({"verificationId": verification_id, "processInstanceKey": key}), 202


@app.get("/verifications/<verification_id>")
def get_verification(verification_id):
    record = _get_verification(verification_id)
    if record is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(record)


@app.post("/verifications/<verification_id>/peer-response")
def receive_peer_response(verification_id):
    """
    Called by external peers to submit their verdict.
    Body: { "peerId": "...", "approved": true/false, "reason": "..." }
    """
    if _get_verification(verification_id) is None:
        return jsonify({"error": "not found"}), 404

    body = request.get_json(force=True)
    peer_id = body.get("peerId") or f"manual-peer-{uuid.uuid4().hex[:8]}"
    approved = _coerce_bool(body.get("approved"), False)
    reason = body.get("reason", "")

    record = _submit_peer_verdict_sync(
        verification_id,
        peer_id=peer_id,
        approved=approved,
        reason=reason,
        source="manual",
    )
    if record is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"received": True, "status": record["status"], "peerVerdicts": record["peerVerdicts"]}), 200


async def _run_workers():
    global _loop
    _loop = asyncio.get_running_loop()
    _loop_ready.set()

    channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
    worker = ZeebeWorker(channel)

    @worker.task(task_type="check-user-registration")
    async def handle_check_registration(verificationId: str, userId: str, **kwargs) -> dict:
        """
        Calls user-service to verify the requester is a registered user.
        Output variables: userRegistered
        """
        log.info("[check-user-registration] verificationId=%s userId=%s", verificationId, userId)
        try:
            resp = http.get(f"{USER_SERVICE_URL}/users/{userId}", timeout=5)
            registered = resp.status_code == 200 and resp.json().get("registered", False)
        except Exception as exc:
            log.warning("[check-user-registration] user-service unreachable: %s", exc)
            registered = False

        if not registered:
            _update_verification(verificationId, status="rejected-unregistered")

        return {"userRegistered": registered}

    @worker.task(task_type="send-verification-request")
    async def handle_send_request(verificationId: str, contentUrl: str, **kwargs) -> dict:
        """
        Sends the content to peers for verification.
        Output variables: peersSent, peerIds
        """
        record = _get_verification(verificationId)
        if record is None:
            return {"peersSent": 0, "peerIds": []}

        peer_ids = [f"peer-{index}" for index in range(1, record["requestedPeerCount"] + 1)]
        _update_verification(
            verificationId,
            status="waiting-for-peers",
            peerIds=peer_ids,
        )

        log.info("[send-verification-request] verificationId=%s url=%s peers=%s", verificationId, contentUrl, peer_ids)

        if record["peerMode"] != "manual":
            asyncio.create_task(_simulate_peer_responses(verificationId))

        return {"peersSent": len(peer_ids), "peerIds": peer_ids}

    @worker.task(task_type="internal-verification")
    async def handle_internal_verification(
        verificationId: str,
        forceInternalFailure: bool = False,
        **kwargs,
    ) -> dict:
        """
        Runs internal content checks after the peer phase.
        Output variables: internalCheckPassed
        """
        passed = not forceInternalFailure
        status = "peer-approved" if passed else "rejected-internal"
        _update_verification(verificationId, status=status)
        log.info("[internal-verification] verificationId=%s passed=%s", verificationId, passed)
        return {"internalCheckPassed": passed}

    @worker.task(task_type="publish-signature")
    async def handle_publish_signature(verificationId: str, contentUrl: str, userId: str, **kwargs) -> dict:
        """
        Generates a deterministic in-memory signature and stores it via attestation-service.
        Output variables: signatureId, signatureHash
        """
        signature_id = str(uuid.uuid4())
        signature_hash = hashlib.sha256(f"{verificationId}|{contentUrl}".encode("utf-8")).hexdigest()

        try:
            resp = http.post(
                f"{ATTESTATION_SERVICE_URL}/attestations",
                json={
                    "verificationId": verificationId,
                    "userId": userId,
                    "contentUrl": contentUrl,
                    "signatureId": signature_id,
                    "signatureHash": signature_hash,
                },
                timeout=5,
            )
            resp.raise_for_status()
        except Exception as exc:
            log.warning("[publish-signature] attestation-service unavailable: %s", exc)

        _update_verification(
            verificationId,
            status="verified",
            signatureId=signature_id,
            signatureHash=signature_hash,
        )

        log.info("[publish-signature] verificationId=%s signatureId=%s", verificationId, signature_id)
        return {"signatureId": signature_id, "signatureHash": signature_hash}

    @worker.task(task_type="send-verification-notification")
    async def handle_send_notification(
        verificationId: str,
        userId: str,
        internalCheckPassed: bool = False,
        userRegistered: bool = True,
        signatureId: str | None = None,
        **kwargs,
    ) -> dict:
        """
        Notifies the requester of the verification outcome and finalizes local state.
        """
        record = _get_verification(verificationId) or {}
        current_status = record.get("status", "pending")

        if not userRegistered:
            status = "rejected-unregistered"
            notif_type = "verification-rejected"
            message = "Verification was rejected because the user is not registered."
        elif signatureId or current_status == "verified" or internalCheckPassed:
            status = "verified"
            notif_type = "verification-verified"
            message = "Verification completed successfully and a signature was published."
        elif current_status == "rejected-peer":
            status = "rejected-peer"
            notif_type = "verification-rejected"
            message = "Verification was rejected by peers."
        elif current_status == "rejected-internal":
            status = "rejected-internal"
            notif_type = "verification-rejected"
            message = "Verification failed during internal checks."
        else:
            status = "timed-out"
            notif_type = "verification-timeout"
            message = "Verification timed out before enough peer approvals were received."

        _update_verification(verificationId, status=status)

        _send_notification(
            userId,
            notif_type,
            message,
            {
                "verificationId": verificationId,
                "status": status,
                "signatureId": signatureId or record.get("signatureId"),
            },
        )

        log.info("[send-verification-notification] userId=%s verificationId=%s status=%s", userId, verificationId, status)
        return {"notificationSent": True, "verificationStatus": status}

    log.info("Zeebe workers started, connecting to %s", ZEEBE_ADDRESS)
    await worker.work()


def _start_worker_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run_workers())


threading.Thread(target=_start_worker_thread, daemon=True, name="zeebe-workers").start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002)
