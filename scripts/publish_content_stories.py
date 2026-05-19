#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import time
from typing import Callable

from confluent_kafka import Producer
import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish curated content lifecycle stories to Kafka")
    parser.add_argument("--kafka-bootstrap-servers", default="localhost:9092")
    parser.add_argument("--content-ledger-url", default="http://localhost:8085")
    parser.add_argument("--sla-monitor-url", default="http://localhost:8005")
    parser.add_argument("--step-delay-seconds", type=float, default=2.0)
    parser.add_argument("--story-gap-seconds", type=float, default=1.0)
    parser.add_argument("--settle-seconds", type=float, default=6.0)
    parser.add_argument("--sla-seconds", type=int, default=72 * 60 * 60)
    parser.add_argument("--skip-query", action="store_true")
    return parser.parse_args()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def build_producer(bootstrap_servers: str) -> Producer:
    return Producer(
        {
            "bootstrap.servers": bootstrap_servers,
            "client.id": "publish-content-stories",
            "acks": "all",
            "enable.idempotence": True,
        }
    )


def publish_json(producer: Producer, topic: str, key: str, payload: dict) -> None:
    producer.produce(topic=topic, key=key.encode("utf-8"), value=json.dumps(payload))


def verification_event(content_id: str, status: str, event_time: datetime, suffix: str) -> tuple[str, str, dict]:
    verification_id = f"{content_id}-verification-{suffix}"
    payload = {
        "eventTime": isoformat(event_time),
        "userId": actor_id_for_content(content_id),
        "contentId": content_id,
        "status": status,
        "type": "verification-verified" if status == "verified" else "verification-rejected",
        "message": f"{content_id} verification -> {status}",
        "payload": {
            "verificationId": verification_id,
            "status": status,
            "contentId": content_id,
            "signatureId": f"signature-{content_id}" if status == "verified" else None,
            "contentTitle": content_id.replace("-", " "),
            "contentUrl": f"https://example.com/{content_id}",
        },
    }
    return "verification-notification", verification_id, payload


def report_event(content_id: str, report_id: str, status: str, event_time: datetime, reason: str) -> tuple[str, str, dict]:
    payload = {
        "eventTime": isoformat(event_time),
        "userId": reporter_id_for_content(content_id),
        "contentId": content_id,
        "status": status,
        "type": status,
        "message": f"{content_id} report -> {status}",
        "payload": {
            "reportId": report_id,
            "postId": content_id,
            "contentId": content_id,
            "reason": reason,
        },
    }
    return "report-notification", report_id, payload


def outcome_event(content_id: str, report_id: str, status: str, event_time: datetime) -> tuple[str, str, dict]:
    payload = {
        "eventTime": isoformat(event_time),
        "reportId": report_id,
        "postId": content_id,
        "contentId": content_id,
        "status": status,
        "postOwnerId": actor_id_for_content(content_id),
    }
    return status, content_id, payload


def content_number(content_id: str) -> str:
    return content_id.split("-", 1)[1] if "-" in content_id else content_id


def actor_id_for_content(content_id: str) -> str:
    return f"user-{content_number(content_id)}"


def reporter_id_for_content(content_id: str) -> str:
    try:
        number = int(content_number(content_id))
    except ValueError:
        return f"user-{content_number(content_id)}-reporter"
    return f"user-{number + 100}"


def build_story_events(sla_seconds: int) -> list[dict]:
    now = utc_now()
    stories = [
        {
            "contentId": "content-1",
            "label": "Verified only",
            "expectedLedgerState": "VERIFIED",
            "expectedSla": "no case",
            "events": [verification_event("content-1", "verified", now - timedelta(minutes=10), "1")],
        },
        {
            "contentId": "content-2",
            "label": "Peer rejected",
            "expectedLedgerState": "REJECTED",
            "expectedSla": "no case",
            "events": [
                verification_event("content-2", "rejected-peer", now - timedelta(minutes=9), "1"),
            ],
        },
        {
            "contentId": "content-3",
            "label": "Verified, accepted report, deleted",
            "expectedLedgerState": "DELETED",
            "expectedSla": "resolved within SLA",
            "events": [
                verification_event("content-3", "verified", now - timedelta(minutes=8), "1"),
                report_event(
                    "content-3",
                    "content-3-report-1",
                    "report-accepted",
                    now - timedelta(minutes=7, seconds=30),
                    "spam",
                ),
                outcome_event(
                    "content-3",
                    "content-3-report-1",
                    "post-deleted",
                    now - timedelta(minutes=7),
                ),
            ],
        },
        {
            "contentId": "content-4",
            "label": "Verified, accepted report, deleted",
            "expectedLedgerState": "DELETED",
            "expectedSla": "resolved within SLA",
            "events": [
                verification_event("content-4", "verified", now - timedelta(minutes=6), "1"),
                report_event(
                    "content-4",
                    "content-4-report-1",
                    "report-accepted",
                    now - timedelta(minutes=5, seconds=50),
                    "copyright",
                ),
                outcome_event(
                    "content-4",
                    "content-4-report-1",
                    "post-deleted",
                    now - timedelta(minutes=5, seconds=20),
                ),
            ],
        },
        {
            "contentId": "content-5",
            "label": "Verified, accepted report, deleted after second review",
            "expectedLedgerState": "DELETED",
            "expectedSla": "resolved within SLA",
            "events": [
                verification_event("content-5", "verified", now - timedelta(minutes=5, seconds=10), "1"),
                report_event(
                    "content-5",
                    "content-5-report-1",
                    "report-accepted",
                    now - timedelta(minutes=4, seconds=35),
                    "false positive",
                ),
                outcome_event(
                    "content-5",
                    "content-5-report-1",
                    "post-deleted",
                    now - timedelta(minutes=4, seconds=5),
                ),
            ],
        },
        {
            "contentId": "content-6",
            "label": "Verified, dismissed report, later accepted report, deleted",
            "expectedLedgerState": "DELETED",
            "expectedSla": "resolved within SLA for accepted report",
            "events": [
                verification_event("content-6", "verified", now - timedelta(minutes=4, seconds=50), "1"),
                report_event(
                    "content-6",
                    "content-6-report-1",
                    "report-dismissed",
                    now - timedelta(minutes=4, seconds=20),
                    "low quality complaint rejected",
                ),
                report_event(
                    "content-6",
                    "content-6-report-2",
                    "report-accepted",
                    now - timedelta(minutes=3, seconds=50),
                    "policy violation",
                ),
                outcome_event(
                    "content-6",
                    "content-6-report-2",
                    "post-deleted",
                    now - timedelta(minutes=3, seconds=10),
                ),
            ],
        },
        {
            "contentId": "content-7",
            "label": "Dismissed report, accepted report, deleted",
            "expectedLedgerState": "DELETED",
            "expectedSla": "resolved within SLA",
            "events": [
                verification_event("content-7", "verified", now - timedelta(minutes=3, seconds=20), "1"),
                report_event(
                    "content-7",
                    "content-7-report-1",
                    "report-dismissed",
                    now - timedelta(minutes=2, seconds=55),
                    "insufficient evidence",
                ),
                report_event(
                    "content-7",
                    "content-7-report-2",
                    "report-accepted",
                    now - timedelta(minutes=2, seconds=30),
                    "stronger complaint later accepted",
                ),
                outcome_event(
                    "content-7",
                    "content-7-report-2",
                    "post-deleted",
                    now - timedelta(minutes=2, seconds=5),
                ),
            ],
        },
        {
            "contentId": "content-8",
            "label": "Verified, accepted report still open",
            "expectedLedgerState": "REPORTED_OPEN",
            "expectedSla": "open case within SLA",
            "events": [
                verification_event("content-8", "verified", now - timedelta(minutes=2, seconds=40), "1"),
                report_event(
                    "content-8",
                    "content-8-report-1",
                    "report-accepted",
                    now - timedelta(minutes=2),
                    "investigation pending",
                ),
            ],
        },
        {
            "contentId": "content-9",
            "label": "Verified, accepted report overdue with no outcome",
            "expectedLedgerState": "REPORTED_OPEN",
            "expectedSla": "open breach",
            "events": [
                verification_event("content-9", "verified", now - timedelta(days=4), "1"),
                report_event(
                    "content-9",
                    "content-9-report-1",
                    "report-accepted",
                    now - timedelta(seconds=sla_seconds + 3600),
                    "moderation stuck",
                ),
            ],
        },
    ]
    return stories


def print_json(title: str, payload: dict) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2))


def fetch_json(url: str, *, timeout: float = 10.0) -> dict:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def wait_until(description: str, predicate: Callable[[], bool], *, timeout_seconds: float, interval_seconds: float = 1.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval_seconds)
    raise RuntimeError(f"Timed out waiting for {description}")


def wait_for_ledger_ready(content_ledger_url: str) -> None:
    def _ready() -> bool:
        try:
            payload = fetch_json(f"{content_ledger_url}/api/health/stream", timeout=5)
        except requests.RequestException:
            return False
        return bool(payload.get("ready"))

    wait_until("content-ledger stream readiness", _ready, timeout_seconds=45)


def fetch_json_with_retry(url: str, *, timeout_seconds: float = 20.0, interval_seconds: float = 1.0) -> dict:
    last_error: Exception | None = None

    def _attempt() -> bool:
        nonlocal last_error
        try:
            fetch_json_with_retry.result = fetch_json(url, timeout=5)
            return True
        except requests.RequestException as exc:
            last_error = exc
            return False

    fetch_json_with_retry.result = None  # type: ignore[attr-defined]
    try:
        wait_until(f"successful GET {url}", _attempt, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)
    except RuntimeError as exc:
        if last_error is not None:
            raise last_error from exc
        raise
    return fetch_json_with_retry.result  # type: ignore[attr-defined]


def publish_story_sequence(producer: Producer, stories: list[dict], *, step_delay_seconds: float, story_gap_seconds: float) -> Counter[str]:
    counters: Counter[str] = Counter()
    for story in stories:
        print(
            f"\n--- publishing {story['contentId']} :: {story['label']} "
            f"(ledger={story['expectedLedgerState']} | sla={story['expectedSla']}) ---"
        )
        for index, (topic, key, payload) in enumerate(story["events"], start=1):
            publish_json(producer, topic, key, payload)
            producer.flush(10)
            counters[topic] += 1
            if payload.get("status"):
                counters[payload["status"]] += 1
            print(
                f"  step {index}: topic={topic} contentId={payload.get('contentId')} "
                f"status={payload.get('status')}"
            )
            if step_delay_seconds > 0:
                time.sleep(step_delay_seconds)
        if story_gap_seconds > 0:
            time.sleep(story_gap_seconds)
    return counters


def main() -> int:
    args = parse_args()
    producer = build_producer(args.kafka_bootstrap_servers)
    stories = build_story_events(args.sla_seconds)
    counters = publish_story_sequence(
        producer,
        stories,
        step_delay_seconds=args.step_delay_seconds,
        story_gap_seconds=args.story_gap_seconds,
    )

    print("=== content story summary ===")
    print(f"kafka_bootstrap_servers: {args.kafka_bootstrap_servers}")
    print(f"stories: {len(stories)}")
    print(f"events_published: {sum(len(story['events']) for story in stories)}")
    print(f"verification_notifications: {counters['verification-notification']}")
    print(f"report_notifications: {counters['report-notification']}")
    print(f"post_deleted: {counters['post-deleted']}")
    print(f"objection_approved: {counters['objection-approved']}")

    print("\n=== expected stories ===")
    for story in stories:
        print(
            f"- {story['contentId']}: {story['label']} | "
            f"ledger={story['expectedLedgerState']} | sla={story['expectedSla']}"
        )

    if args.skip_query:
        return 0

    time.sleep(args.settle_seconds)
    wait_for_ledger_ready(args.content_ledger_url)

    print_json(
        "content ledger list",
        fetch_json_with_retry(f"{args.content_ledger_url}/api/content?limit=50&withState=true"),
    )
    print_json("sla open cases", fetch_json_with_retry(f"{args.sla_monitor_url}/sla/open-cases"))
    print_json(
        "sla violations",
        fetch_json_with_retry(f"{args.sla_monitor_url}/sla/violations?include_resolved=true"),
    )

    for story in stories:
        content_id = story["contentId"]
        print_json(
            f"ledger state :: {content_id}",
            fetch_json_with_retry(f"{args.content_ledger_url}/api/content/{content_id}/state"),
        )
        print_json(
            f"ledger trace :: {content_id}",
            fetch_json_with_retry(f"{args.content_ledger_url}/api/content/{content_id}/decision-trace"),
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
