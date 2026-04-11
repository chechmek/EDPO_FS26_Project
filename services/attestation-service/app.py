"""
Attestation Service
===================
Stores published verification signatures in memory.
"""

import logging
import threading
import uuid
from typing import Any

from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("attestation-service")

app = Flask(__name__)

_attestations: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _serialize(record: dict[str, Any]) -> dict[str, Any]:
    return dict(record)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "attestation-service"})


@app.post("/attestations")
def create_attestation():
    body = request.get_json(force=True)
    verification_id = body.get("verificationId")
    user_id = body.get("userId")
    content_url = body.get("contentUrl")
    signature_hash = body.get("signatureHash")
    if not verification_id or not user_id or not content_url or not signature_hash:
        return jsonify({"error": "verificationId, userId, contentUrl, and signatureHash are required"}), 400

    signature_id = body.get("signatureId") or str(uuid.uuid4())
    record = {
        "signatureId": signature_id,
        "verificationId": verification_id,
        "userId": user_id,
        "contentUrl": content_url,
        "signatureHash": signature_hash,
        "isValid": True,
        "invalidatedBy": None,
        "invalidatedReason": None,
    }

    with _lock:
        _attestations[signature_id] = record

    log.info("[create-attestation] signatureId=%s verificationId=%s", signature_id, verification_id)
    return jsonify(record), 201


@app.get("/attestations")
def list_attestations():
    with _lock:
        items = [_serialize(item) for item in _attestations.values()]
    return jsonify({"count": len(items), "attestations": items})


@app.get("/attestations/<signature_id>")
def get_attestation(signature_id):
    with _lock:
        record = _attestations.get(signature_id)
    if record is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(_serialize(record))


@app.post("/attestations/invalidate")
def invalidate_attestation():
    body = request.get_json(force=True)
    signature_id = body.get("signatureId")
    content_url = body.get("contentUrl")
    invalidated_by = body.get("invalidatedBy", "reporting-service")
    reason = body.get("reason", "Post deleted by moderation")
    if not signature_id and not content_url:
        return jsonify({"error": "signatureId or contentUrl is required"}), 400

    updated: list[dict[str, Any]] = []
    with _lock:
        for record in _attestations.values():
            if signature_id and record.get("signatureId") != signature_id:
                continue
            if content_url and record.get("contentUrl") != content_url:
                continue
            record["isValid"] = False
            record["invalidatedBy"] = invalidated_by
            record["invalidatedReason"] = reason
            updated.append(_serialize(record))

    if not updated:
        return jsonify({"invalidated": 0, "attestations": []}), 404

    log.info(
        "[invalidate-attestation] signatureId=%s contentUrl=%s count=%s",
        signature_id,
        content_url,
        len(updated),
    )
    return jsonify({"invalidated": len(updated), "attestations": updated}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8004)
