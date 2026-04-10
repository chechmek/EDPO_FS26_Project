"""
User Service
============
Handles user registration (REST + Camunda workers for RegisterUser.bpmn).

Zeebe job types owned by this service:
  - register-user -> "Register User" service task
  - reject-user   -> "Reject User" service task

REST endpoints:
  POST /users
  GET  /users/<id>
  GET  /health
"""

import asyncio
import logging
import os
import threading
import uuid
from typing import Any

from flask import Flask, jsonify, request
from pyzeebe import ZeebeClient, ZeebeWorker, create_insecure_channel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("user-service")

app = Flask(__name__)

ZEEBE_ADDRESS = os.getenv("ZEEBE_ADDRESS", "zeebe:26500")
REGISTER_USER_PROCESS_ID = "Process_1kwkl0j"

_users: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

_loop: asyncio.AbstractEventLoop | None = None
_loop_ready = threading.Event()


def _zeebe_call(coro):
    """Submit a coroutine to the shared worker loop and block until done."""
    _loop_ready.wait()
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _get_user(user_id: str) -> dict[str, Any] | None:
    with _lock:
        record = _users.get(user_id)
        return None if record is None else dict(record)


def _upsert_user(user_id: str, **fields: Any) -> dict[str, Any]:
    with _lock:
        record = _users.setdefault(user_id, {"id": user_id, "registered": False, "status": "pending"})
        record.update(fields)
        return dict(record)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "user-service"})


@app.post("/users")
def register_user():
    """
    Starts the RegisterUser Camunda process.
    Body: { "username": "...", "password": "..." }
    """
    body = request.get_json(force=True)
    username = body.get("username")
    if not username:
        return jsonify({"error": "username required"}), 400

    user_id = str(uuid.uuid4())
    password = body.get("password", "")
    simulated_pass = _coerce_bool(body.get("simulateBackgroundPass", True))

    _upsert_user(
        user_id,
        username=username,
        registered=False,
        status="pending",
        rejectionReason=None,
    )

    variables = {
        "userId": user_id,
        "username": username,
        "passwordHash": f"in-memory::{password}" if password else "in-memory::empty",
        "simulateBackgroundPass": simulated_pass,
    }

    async def _start():
        channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
        client = ZeebeClient(channel)
        instance = await client.run_process(REGISTER_USER_PROCESS_ID, variables=variables)
        await channel.close()
        return instance.process_instance_key

    key = _zeebe_call(_start())
    return jsonify(
        {
            "userId": user_id,
            "processInstanceKey": key,
            "message": "Registration process started",
        }
    ), 202


@app.get("/users/<user_id>")
def get_user(user_id):
    """
    Called by verification-service to check if a user is registered.
    Returns { "registered": true/false, "status": "..." }
    """
    user = _get_user(user_id)
    if user is None:
        return jsonify({"id": user_id, "registered": False, "status": "unknown"}), 404
    return jsonify(user)


async def _run_workers():
    global _loop
    _loop = asyncio.get_running_loop()
    _loop_ready.set()

    channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
    worker = ZeebeWorker(channel)

    @worker.task(task_type="register-user")
    async def handle_register_user(userId: str, username: str, passwordHash: str, **kwargs) -> dict:
        """
        Marks the pending user as registered.
        Input variables: userId, username, passwordHash
        Output variables: userId, registered
        """
        log.info("[register-user] userId=%s username=%s", userId, username)
        _upsert_user(
            userId,
            username=username,
            passwordHash=passwordHash,
            registered=True,
            status="registered",
            rejectionReason=None,
        )
        return {"userId": userId, "registered": True}

    @worker.task(task_type="reject-user")
    async def handle_reject_user(userId: str, username: str, **kwargs) -> dict:
        """
        Marks the pending user as rejected.
        Input variables: userId, username
        Output variables: userId, registered
        """
        log.info("[reject-user] userId=%s username=%s", userId, username)
        _upsert_user(
            userId,
            username=username,
            registered=False,
            status="rejected",
            rejectionReason="Background check failed",
        )
        return {"userId": userId, "registered": False}

    log.info("Zeebe workers started, connecting to %s", ZEEBE_ADDRESS)
    await worker.work()


def _start_worker_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run_workers())


threading.Thread(target=_start_worker_thread, daemon=True, name="zeebe-workers").start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001)
