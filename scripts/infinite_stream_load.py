#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import random
import signal
import sys
import time

from confluent_kafka import Producer


@dataclass
class ContentRecord:
    content_id: str
    owner_user_id: str
    reporter_user_id: str | None = None
    verification_status: str | None = None
    report_counter: int = 0
    open_report_id: str | None = None
    deleted: bool = False
    last_report_status: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Infinite Kafka load for content-ledger + sla-monitor")
    parser.add_argument("--kafka-bootstrap-servers", default="localhost:9092")
    parser.add_argument("--sleep-seconds", type=float, default=1.5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-contents", type=int, default=50)
    parser.add_argument("--start-content-number", type=int, default=100)
    parser.add_argument("--start-user-number", type=int, default=100)
    parser.add_argument("--new-content-probability", type=float, default=0.35)
    parser.add_argument("--report-probability", type=float, default=0.45)
    parser.add_argument("--dismiss-report-probability", type=float, default=0.25)
    parser.add_argument("--delete-after-accepted-probability", type=float, default=0.7)
    parser.add_argument("--objection-approved-probability", type=float, default=0.0)
    parser.add_argument("--open-report-probability", type=float, default=0.3)
    parser.add_argument("--overdue-report-probability", type=float, default=0.08)
    parser.add_argument("--verification-verified-probability", type=float, default=0.7)
    parser.add_argument("--verification-peer-rejected-probability", type=float, default=0.15)
    parser.add_argument("--verification-internal-rejected-probability", type=float, default=0.1)
    parser.add_argument("--verification-unregistered-probability", type=float, default=0.03)
    parser.add_argument("--verification-timeout-probability", type=float, default=0.02)
    parser.add_argument("--sla-seconds", type=int, default=72 * 60 * 60)
    return parser.parse_args()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def clamp_probability(value: float, name: str) -> float:
    if value < 0 or value > 1:
        raise SystemExit(f"{name} must be between 0 and 1")
    return value


def build_producer(bootstrap_servers: str) -> Producer:
    return Producer(
        {
            "bootstrap.servers": bootstrap_servers,
            "client.id": "infinite-stream-load",
            "acks": "all",
            "enable.idempotence": True,
        }
    )


def publish_json(producer: Producer, topic: str, key: str, payload: dict) -> None:
    producer.produce(topic=topic, key=key.encode("utf-8"), value=json.dumps(payload))
    producer.flush(10)


def verification_event(content: ContentRecord, status: str) -> tuple[str, str, dict]:
    verification_id = f"{content.content_id}-verification-1"
    return (
        "verification-notification",
        verification_id,
        {
            "eventTime": isoformat(utc_now()),
            "userId": content.owner_user_id,
            "contentId": content.content_id,
            "status": status,
            "type": "verification-verified" if status == "verified" else "verification-rejected",
            "message": f"{content.content_id} verification -> {status}",
            "payload": {
                "verificationId": verification_id,
                "status": status,
                "contentId": content.content_id,
                "signatureId": f"signature-{content.content_id}" if status == "verified" else None,
                "contentTitle": content.content_id,
                "contentUrl": f"https://example.com/{content.content_id}",
            },
        },
    )


def report_event(content: ContentRecord, status: str, *, event_time: datetime | None = None, reason: str = "") -> tuple[str, str, dict]:
    report_id = f"{content.content_id}-report-{content.report_counter}"
    return (
        "report-notification",
        report_id,
        {
            "eventTime": isoformat(event_time or utc_now()),
            "userId": content.reporter_user_id or f"user-{content.owner_user_id.split('-', 1)[1]}",
            "contentId": content.content_id,
            "status": status,
            "type": status,
            "message": f"{content.content_id} report -> {status}",
            "payload": {
                "reportId": report_id,
                "postId": content.content_id,
                "contentId": content.content_id,
                "reason": reason or status,
            },
        },
    )


def outcome_event(content: ContentRecord, status: str) -> tuple[str, str, dict]:
    assert content.open_report_id is not None
    return (
        status,
        content.content_id,
        {
            "eventTime": isoformat(utc_now()),
            "reportId": content.open_report_id,
            "postId": content.content_id,
            "contentId": content.content_id,
            "status": status,
            "postOwnerId": content.owner_user_id,
        },
    )


def choose_verification_status(args: argparse.Namespace) -> str:
    choices = [
        ("verified", args.verification_verified_probability),
        ("rejected-peer", args.verification_peer_rejected_probability),
        ("rejected-internal", args.verification_internal_rejected_probability),
        ("rejected-unregistered", args.verification_unregistered_probability),
        ("timed-out", args.verification_timeout_probability),
    ]
    total = sum(weight for _, weight in choices)
    if total <= 0:
        raise SystemExit("verification probabilities must sum to > 0")
    return random.choices([name for name, _ in choices], weights=[weight for _, weight in choices], k=1)[0]


def log_event(step: int, topic: str, payload: dict) -> None:
    print(
        f"[{step:05d}] topic={topic:<24} "
        f"contentId={payload.get('contentId', payload.get('payload', {}).get('contentId', '—')):<12} "
        f"status={payload.get('status', '—')}"
    )


def main() -> int:
    args = parse_args()
    for name in (
        "--new-content-probability",
        "--report-probability",
        "--dismiss-report-probability",
        "--delete-after-accepted-probability",
        "--objection-approved-probability",
        "--open-report-probability",
        "--overdue-report-probability",
    ):
        clamp_probability(getattr(args, name[2:].replace("-", "_")), name)

    random.seed(args.seed)
    producer = build_producer(args.kafka_bootstrap_servers)
    content_counter = args.start_content_number
    user_counter = args.start_user_number
    step = 0
    contents: list[ContentRecord] = []
    stopped = False

    def _stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while not stopped:
        step += 1
        verified_candidates = [item for item in contents if item.verification_status == "verified" and not item.deleted]
        open_cases = [item for item in verified_candidates if item.open_report_id is not None]
        reportable = [item for item in verified_candidates if item.open_report_id is None]

        should_create = (
            not contents
            or len(contents) < args.max_contents and random.random() < args.new_content_probability
        )

        if should_create:
            content_id = f"content-{content_counter}"
            owner_user_id = f"user-{user_counter}"
            reporter_user_id = f"user-{user_counter + 1000}"
            content_counter += 1
            user_counter += 1

            content = ContentRecord(
                content_id=content_id,
                owner_user_id=owner_user_id,
                reporter_user_id=reporter_user_id,
            )
            status = choose_verification_status(args)
            topic, key, payload = verification_event(content, status)
            publish_json(producer, topic, key, payload)
            content.verification_status = status
            contents.append(content)
            log_event(step, topic, payload)
            time.sleep(args.sleep_seconds)
            continue

        if reportable and random.random() < args.report_probability:
            content = random.choice(reportable)
            content.report_counter += 1
            content.open_report_id = f"{content.content_id}-report-{content.report_counter}"

            if random.random() < args.dismiss_report_probability:
                topic, key, payload = report_event(content, "report-dismissed", reason="moderator dismissed complaint")
                content.last_report_status = "report-dismissed"
                content.open_report_id = None
                publish_json(producer, topic, key, payload)
                log_event(step, topic, payload)
                time.sleep(args.sleep_seconds)
                continue

            overdue = random.random() < args.overdue_report_probability
            report_time = utc_now() - timedelta(seconds=args.sla_seconds + 1800) if overdue else utc_now()
            topic, key, payload = report_event(
                content,
                "report-accepted",
                event_time=report_time,
                reason="moderator accepted complaint",
            )
            content.last_report_status = "report-accepted"
            publish_json(producer, topic, key, payload)
            log_event(step, topic, payload)
            time.sleep(args.sleep_seconds)
            continue

        if open_cases:
            content = random.choice(open_cases)
            if random.random() < args.open_report_probability:
                time.sleep(args.sleep_seconds)
                continue

            if random.random() < args.objection_approved_probability:
                status = "objection-approved"
            elif random.random() < args.delete_after_accepted_probability:
                status = "post-deleted"
            else:
                time.sleep(args.sleep_seconds)
                continue

            topic, key, payload = outcome_event(content, status)
            publish_json(producer, topic, key, payload)
            if status == "post-deleted":
                content.deleted = True
            content.open_report_id = None
            content.last_report_status = status
            log_event(step, topic, payload)
            time.sleep(args.sleep_seconds)
            continue

        time.sleep(args.sleep_seconds)

    print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
