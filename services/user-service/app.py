"""
User Service
============
Handles user registration (REST + Camunda workers for RegisterUser.bpmn).

Zeebe job types owned by this service:
  - register-user                  → "Register User" service task
  - send-registration-notification → "Send Notification to User" service tasks

REST endpoints:
  POST /users              → trigger RegisterUser Camunda process
  GET  /users/<id>         → look up a user (called by verification-service)
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
log = logging.getLogger("user-service")

app = Flask(__name__)

ZEEBE_ADDRESS = os.getenv("ZEEBE_ADDRESS", "zeebe:26500")
REGISTER_USER_PROCESS_ID = "Process_1kwkl0j"  # id from RegisterUser.bpmn

# In-memory store — replace with a real DB
_users: dict[str, dict] = {}

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

    variables = {
        "username": username,
        # never put real passwords in process variables — placeholder only
        "passwordHash": "TODO-hash-this",
    }

    async def _start():
        channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
        client = ZeebeClient(channel)
        instance = await client.run_process(REGISTER_USER_PROCESS_ID, variables=variables)
        await channel.close()
        return instance.process_instance_key

    key = _zeebe_call(_start())
    return jsonify({"processInstanceKey": key, "message": "Registration process started"}), 202


@app.get("/users/<user_id>")
def get_user(user_id):
    """
    Called by verification-service to check if a user is registered.
    Returns { "registered": true/false }
    """
    user = _users.get(user_id)
    if user:
        return jsonify({"id": user_id, "registered": True, **user})
    # TODO: replace with real DB lookup
    return jsonify({"id": user_id, "registered": False}), 404


# ---------------------------------------------------------------------------
# Zeebe workers
# ---------------------------------------------------------------------------

async def _run_workers():
    global _loop
    _loop = asyncio.get_running_loop()
    _loop_ready.set()

    channel = create_insecure_channel(grpc_address=ZEEBE_ADDRESS)
    worker = ZeebeWorker(channel)

    @worker.task(task_type="register-user")
    async def handle_register_user(username: str, passwordHash: str, **kwargs) -> dict:
        """
        Persists the new user to the database.
        Input variables:  username, passwordHash
        Output variables: userId, registered
        """
        log.info("[register-user] registering user '%s'", username)
        user_id = str(uuid.uuid4())
        # TODO: persist to DB
        _users[user_id] = {"username": username}
        return {"userId": user_id, "registered": True}

    @worker.task(task_type="reject-user")
    async def handle_reject_user(username: str, **kwargs) -> dict:
        """
        Handles a rejection of user .
        Input variables:  userId, success (bool)
        """
        log.info("[reject-user] username=%s", username)

        # TODO: publish "user rejected event"

        return {}

    log.info("Zeebe workers started, connecting to %s", ZEEBE_ADDRESS)
    await worker.work()


def _start_worker_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run_workers())


threading.Thread(target=_start_worker_thread, daemon=True, name="zeebe-workers").start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001)
