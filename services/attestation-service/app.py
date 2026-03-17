"""
Attestation Service
===================
Periodically re-verifies registered content to detect tampering or changes.
Publishes an 'AttestationFailed' event when content has drifted from its
original verified state.

This service does NOT use Camunda — it runs its own scheduler loop.
If you want to model re-attestation as a Camunda process, add a separate BPMN
and wire up workers here in the same pattern as the other services.

REST endpoints:
  POST /attestations             → register content for ongoing monitoring
  GET  /attestations/<id>        → get attestation record + last check result
  DELETE /attestations/<id>      → stop monitoring this content
  GET  /health

Background job:
  Every ATTESTATION_INTERVAL_SECONDS, fetch each registered content URL,
  compute its hash, and compare against the baseline recorded at registration.
  On mismatch → mark as 'tampered' and publish an event (Kafka / webhook / TODO).
"""

import hashlib
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import requests as http
from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("attestation-service")

app = Flask(__name__)

ATTESTATION_INTERVAL = int(os.getenv("ATTESTATION_INTERVAL_SECONDS", "60"))

# In-memory store — replace with a real DB
# key: attestation_id → {
#   "contentUrl", "baselineHash", "signatureId",
#   "status": "ok"|"tampered"|"unreachable",
#   "lastChecked", "lastHash"
# }
_attestations: dict[str, dict] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "attestation-service"})


@app.post("/attestations")
def register_attestation():
    """
    Registers content for periodic integrity monitoring.
    Body: { "contentUrl": "...", "signatureId": "..." }

    The baseline hash is computed immediately at registration.
    """
    body = request.get_json(force=True)
    content_url = body.get("contentUrl")
    if not content_url:
        return jsonify({"error": "contentUrl required"}), 400

    baseline_hash = _fetch_hash(content_url)
    if baseline_hash is None:
        return jsonify({"error": "could not fetch content at provided URL"}), 422

    attestation_id = str(uuid.uuid4())
    with _lock:
        _attestations[attestation_id] = {
            "contentUrl": content_url,
            "signatureId": body.get("signatureId"),
            "baselineHash": baseline_hash,
            "status": "ok",
            "lastChecked": _now(),
            "lastHash": baseline_hash,
        }

    log.info("[register] attestationId=%s url=%s", attestation_id, content_url)
    return jsonify({"attestationId": attestation_id, "baselineHash": baseline_hash}), 201


@app.get("/attestations/<attestation_id>")
def get_attestation(attestation_id):
    record = _attestations.get(attestation_id)
    if not record:
        return jsonify({"error": "not found"}), 404
    return jsonify({"attestationId": attestation_id, **record})


@app.delete("/attestations/<attestation_id>")
def delete_attestation(attestation_id):
    with _lock:
        removed = _attestations.pop(attestation_id, None)
    if not removed:
        return jsonify({"error": "not found"}), 404
    log.info("[deregister] attestationId=%s", attestation_id)
    return jsonify({"removed": True}), 200


# ---------------------------------------------------------------------------
# Background scheduler
# ---------------------------------------------------------------------------

def _fetch_hash(url: str) -> str | None:
    """Fetches content from a URL and returns its SHA-256 hash, or None on error."""
    try:
        resp = http.get(url, timeout=10)
        resp.raise_for_status()
        return hashlib.sha256(resp.content).hexdigest()
    except Exception as exc:
        log.warning("[fetch-hash] failed for %s: %s", url, exc)
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _on_tampered(attestation_id: str, record: dict):
    """
    Called when content no longer matches its baseline hash.
    TODO: publish 'AttestationFailed' event to Kafka topic.
    """
    log.warning(
        "[TAMPERED] attestationId=%s url=%s baseline=%s current=%s",
        attestation_id, record["contentUrl"], record["baselineHash"], record["lastHash"],
    )
    # TODO: kafka_producer.send("attestation.events", {
    #   "type": "AttestationFailed",
    #   "attestationId": attestation_id,
    #   "contentUrl": record["contentUrl"],
    #   "signatureId": record["signatureId"],
    #   "detectedAt": _now(),
    # })


def _check_loop():
    log.info("Attestation scheduler started (interval=%ss)", ATTESTATION_INTERVAL)
    while True:
        time.sleep(ATTESTATION_INTERVAL)
        with _lock:
            items = list(_attestations.items())

        for attestation_id, record in items:
            current_hash = _fetch_hash(record["contentUrl"])
            now = _now()

            with _lock:
                if attestation_id not in _attestations:
                    continue  # was deleted while we were checking
                entry = _attestations[attestation_id]
                entry["lastChecked"] = now

                if current_hash is None:
                    entry["status"] = "unreachable"
                    log.warning("[check] attestationId=%s unreachable", attestation_id)
                elif current_hash != entry["baselineHash"]:
                    entry["lastHash"] = current_hash
                    entry["status"] = "tampered"
                    _on_tampered(attestation_id, entry)
                else:
                    entry["lastHash"] = current_hash
                    entry["status"] = "ok"
                    log.debug("[check] attestationId=%s ok", attestation_id)


threading.Thread(target=_check_loop, daemon=True, name="attestation-scheduler").start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8004)
