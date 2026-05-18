#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import time
from uuid import uuid4

from confluent_kafka import Producer
import requests

FINAL_VERIFICATION_STATUSES = {
    "verified",
    "rejected-peer",
    "rejected-internal",
    "rejected-unregistered",
    "timed-out",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview load for processors/sla-monitor")
    parser.add_argument("--user-service-url", default="http://localhost:8001")
    parser.add_argument("--verification-service-url", default="http://localhost:8002")
    parser.add_argument("--sla-monitor-url", default="http://localhost:8005")
    parser.add_argument("--kafka-bootstrap-servers", default="localhost:9092")
    parser.add_argument("--verification-approved", type=int, default=4)
    parser.add_argument("--verification-peer-rejected", type=int, default=2)
    parser.add_argument("--verification-internal-rejected", type=int, default=2)
    parser.add_argument("--verification-unregistered", type=int, default=1)
    parser.add_argument("--settle-seconds", type=float, default=8.0)
    parser.add_argument("--sla-seconds", type=int, default=72 * 60 * 60)
    return parser.parse_args()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def create_registered_user(user_service_url: str, username: str) -> str:
    response = requests.post(
        f"{user_service_url}/users",
        json={"username": username, "password": "secret", "simulateBackgroundPass": True},
        timeout=10,
    )
    response.raise_for_status()
    user_id = response.json()["userId"]

    deadline = time.time() + 30
    while time.time() < deadline:
        user_response = requests.get(f"{user_service_url}/users/{user_id}", timeout=10)
        user_response.raise_for_status()
        payload = user_response.json()
        if payload.get("registered") and payload.get("status") == "registered":
            return user_id
        time.sleep(0.5)

    raise RuntimeError(f"user {user_id} did not become registered in time")


def start_verification(
    verification_service_url: str,
    *,
    user_id: str,
    suffix: str,
    peer_mode: str,
    force_internal_failure: bool = False,
) -> str:
    payload = {
        "userId": user_id,
        "contentUrl": f"https://example.com/content/{suffix}",
        "contentTitle": f"Demo content {suffix}",
        "requestedPeerCount": 2,
        "requiredApprovalCount": 2,
        "peerMode": peer_mode,
        "peerResponseDelaySeconds": 0.2,
        "forceInternalFailure": force_internal_failure,
    }
    response = requests.post(f"{verification_service_url}/verifications", json=payload, timeout=10)
    response.raise_for_status()
    return response.json()["verificationId"]


def wait_for_verification_statuses(verification_service_url: str, verification_ids: list[str]) -> dict[str, str]:
    remaining = set(verification_ids)
    statuses: dict[str, str] = {}
    deadline = time.time() + 45

    while remaining and time.time() < deadline:
        for verification_id in list(remaining):
            response = requests.get(f"{verification_service_url}/verifications/{verification_id}", timeout=10)
            response.raise_for_status()
            payload = response.json()
            status = payload.get("status")
            if status in FINAL_VERIFICATION_STATUSES:
                statuses[verification_id] = status
                remaining.remove(verification_id)
        if remaining:
            time.sleep(0.5)

    if remaining:
        raise RuntimeError(f"verifications did not finish in time: {sorted(remaining)}")
    return statuses


def produce_json(producer: Producer, topic: str, key: str, payload: dict) -> None:
    producer.produce(topic=topic, key=key.encode("utf-8"), value=json.dumps(payload))
    producer.flush(5)


def publish_moderation_preview_events(bootstrap_servers: str, sla_seconds: int) -> None:
    producer = Producer(
        {
            "bootstrap.servers": bootstrap_servers,
            "client.id": "preview-sla-monitor",
            "acks": "all",
            "enable.idempotence": True,
        }
    )
    now = utc_now()
    old_opened_at = now - timedelta(seconds=sla_seconds + 5)
    old_opened_at_resolved = now - timedelta(seconds=sla_seconds + 9)

    def report_notification_payload(content_id: str, report_id: str, event_time: datetime) -> dict:
        return {
            "eventTime": isoformat(event_time),
            "userId": f"reporter-{content_id}",
            "contentId": content_id,
            "status": "report-accepted",
            "type": "report-accepted",
            "message": "Synthetic accepted report for SLA monitor preview.",
            "payload": {
                "reportId": report_id,
                "postId": content_id,
                "contentId": content_id,
            },
        }

    def outcome_payload(content_id: str, report_id: str, status: str, event_time: datetime) -> dict:
        return {
            "eventTime": isoformat(event_time),
            "reportId": report_id,
            "postId": content_id,
            "contentId": content_id,
            "status": status,
            "postOwnerId": f"owner-{content_id}",
        }

    produce_json(
        producer,
        "report-notification",
        "preview-within-delete",
        report_notification_payload("preview-within-delete", "report-within-delete", now - timedelta(seconds=30)),
    )
    produce_json(
        producer,
        "post-deleted",
        "preview-within-delete",
        outcome_payload("preview-within-delete", "report-within-delete", "post-deleted", now),
    )

    produce_json(
        producer,
        "report-notification",
        "preview-within-objection",
        report_notification_payload("preview-within-objection", "report-within-objection", now - timedelta(seconds=20)),
    )
    produce_json(
        producer,
        "objection-approved",
        "preview-within-objection",
        outcome_payload("preview-within-objection", "report-within-objection", "objection-approved", now),
    )

    produce_json(
        producer,
        "report-notification",
        "preview-open-breach",
        report_notification_payload("preview-open-breach", "report-open-breach", old_opened_at),
    )

    produce_json(
        producer,
        "report-notification",
        "preview-resolved-breach",
        report_notification_payload("preview-resolved-breach", "report-resolved-breach", old_opened_at_resolved),
    )
    time.sleep(2.0)
    produce_json(
        producer,
        "post-deleted",
        "preview-resolved-breach",
        outcome_payload("preview-resolved-breach", "report-resolved-breach", "post-deleted", now),
    )


def print_json(title: str, payload: dict) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2))


def main() -> int:
    args = parse_args()

    response = requests.get(f"{args.sla_monitor_url}/health", timeout=10)
    response.raise_for_status()
    print_json("sla-monitor health", response.json())

    user_id = create_registered_user(args.user_service_url, f"preview-user-{uuid4().hex[:8]}")
    print(f"\nregistered preview user: {user_id}")

    verification_ids: list[str] = []
    for index in range(args.verification_approved):
        verification_ids.append(
            start_verification(
                args.verification_service_url,
                user_id=user_id,
                suffix=f"verified-{index}-{uuid4().hex[:6]}",
                peer_mode="auto-approve",
            )
        )
    for index in range(args.verification_peer_rejected):
        verification_ids.append(
            start_verification(
                args.verification_service_url,
                user_id=user_id,
                suffix=f"peer-rejected-{index}-{uuid4().hex[:6]}",
                peer_mode="auto-reject",
            )
        )
    for index in range(args.verification_internal_rejected):
        verification_ids.append(
            start_verification(
                args.verification_service_url,
                user_id=user_id,
                suffix=f"internal-rejected-{index}-{uuid4().hex[:6]}",
                peer_mode="auto-approve",
                force_internal_failure=True,
            )
        )
    for index in range(args.verification_unregistered):
        verification_ids.append(
            start_verification(
                args.verification_service_url,
                user_id=str(uuid4()),
                suffix=f"unregistered-{index}-{uuid4().hex[:6]}",
                peer_mode="auto-approve",
            )
        )

    statuses = wait_for_verification_statuses(args.verification_service_url, verification_ids)
    print_json("verification statuses", statuses)

    publish_moderation_preview_events(args.kafka_bootstrap_servers, args.sla_seconds)
    time.sleep(args.settle_seconds)

    metrics_response = requests.get(f"{args.sla_monitor_url}/metrics/verification", timeout=10)
    metrics_response.raise_for_status()
    print_json("verification metrics", metrics_response.json())

    open_cases_response = requests.get(f"{args.sla_monitor_url}/sla/open-cases", timeout=10)
    open_cases_response.raise_for_status()
    print_json("open SLA cases", open_cases_response.json())

    violations_response = requests.get(
        f"{args.sla_monitor_url}/sla/violations",
        params={"include_resolved": "true"},
        timeout=10,
    )
    violations_response.raise_for_status()
    print_json("SLA violations", violations_response.json())

    print(
        "\npreview complete. verification traffic came through the live services; "
        "moderation SLA traffic was injected directly on Kafka because report acceptance remains a human Tasklist step."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
