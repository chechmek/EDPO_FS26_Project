from __future__ import annotations

import logging
import os

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("ui-service")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
CONTENT_LEDGER_BASE_URL = os.getenv("CONTENT_LEDGER_BASE_URL", "http://content-ledger:8085")
SLA_MONITOR_BASE_URL = os.getenv("SLA_MONITOR_BASE_URL", "http://sla-monitor:8005")
PORT = int(os.getenv("PORT", "8086"))
PROXY_TIMEOUT_SECONDS = float(os.getenv("PROXY_TIMEOUT_SECONDS", "10"))

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")


def _proxy_get(base_url: str, path: str) -> Response:
    upstream = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        resp = requests.get(
            upstream,
            params=request.args,
            headers={"Accept": request.headers.get("Accept", "*/*")},
            timeout=PROXY_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        log.warning("proxy failed upstream=%s error=%s", upstream, exc)
        return jsonify({"error": "upstream_unreachable", "upstream": upstream, "detail": str(exc)}), 502

    excluded = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    headers = [(name, value) for name, value in resp.headers.items() if name.lower() not in excluded]
    return Response(resp.content, status=resp.status_code, headers=headers)


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "ui-service",
            "contentLedgerBaseUrl": CONTENT_LEDGER_BASE_URL,
            "slaMonitorBaseUrl": SLA_MONITOR_BASE_URL,
        }
    )


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/styles.css")
def styles():
    return send_from_directory(STATIC_DIR, "styles.css")


@app.get("/app.js")
def app_js():
    return send_from_directory(STATIC_DIR, "app.js")


@app.get("/api/<path:path>")
def ledger_api_proxy(path: str):
    return _proxy_get(CONTENT_LEDGER_BASE_URL, f"/api/{path}")


@app.get("/actuator/<path:path>")
def ledger_actuator_proxy(path: str):
    return _proxy_get(CONTENT_LEDGER_BASE_URL, f"/actuator/{path}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
