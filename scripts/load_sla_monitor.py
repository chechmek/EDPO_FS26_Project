#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import random
import time
from uuid import uuid4

from confluent_kafka import Producer
import requests

VERIFICATION_STATUS_TO_TYPE = {
    "verified": "verification-verified",
    "rejected-peer": "verification-rejected",
    "rejected-internal": "verification-rejected",
    "rejected-unregistered": "verification-rejected",
    "timed-out": "verification-timeout",
}

RESOLUTION_STATUSES = ("post-deleted", "objection-approved")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Kafka load for processors/sla-monitor")
    parser.add_argument("--kafka-bootstrap-servers", default="localhost:9092")
    parser.add_argument("--sla-monitor-url", default="http://localhost:8005")
    parser.add_argument("--verification-events", type=int, default=200)
    parser.add_argument("--report-cases", type=int, default=80)
    parser.add_argument("--breach-ratio", type=float, default=0.15)
    parser.add_argument("--resolved-ratio", type=float, default=0.7)
    parser.add_argument("--out-of-order-ratio", type=float, default=0.25)
    parser.add_argument("--max-report-age-seconds", type=int, default=3600)
    parser.add_argument("--sla-seconds", type=int, default=72 * 60 * 60)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--pause-between-batches", type=float, default=0.0)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    parser.add_argument("--skip-query", action="store_true")
    return parser.parse_args()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def clamp_ratio(value: float, name: str) -> float:
    if value < 0 or value > 1:
        raise SystemExit(f"{name} must be between 0 and 1")
    return value


def build_producer(bootstrap_servers: str) -> Producer:
    return Producer(
        {
            "bootstrap.servers": bootstrap_servers,
            "client.id": "load-sla-monitor",
            "acks": "all",
            "enable.idempotence": True,
        }
    )


def publish_json(producer: Producer, topic: str, key: str, payload: dict) -> None:
    producer.produce(topic=topic, key=key.encode("utf-8"), value=json.dumps(payload))


def verification_payload(status: str, event_time: datetime, index: int) -> tuple[str, str, dict]:
    verification_id = f"load-verification-{index}-{uuid4().hex[:8]}"
    content_id = f"load-content-{uuid4().hex[:10]}"
    payload = {
        "eventTime": isoformat(event_time),
        "userId": f"load-user-{random.randint(1, max(10, index + 1))}",
        "contentId": content_id,
        "status": status,
        "type": VERIFICATION_STATUS_TO_TYPE[status],
        "message": f"Synthetic verification event with status={status}",
        "payload": {
            "verificationId": verification_id,
            "status": status,
            "contentId": content_id,
            "signatureId": f"signature-{uuid4().hex[:8]}" if status == "verified" else None,
        },
    }
    return "verification-notification", verification_id, payload


def report_notification_payload(content_id: str, report_id: str, event_time: datetime) -> tuple[str, str, dict]:
    payload = {
        "eventTime": isoformat(event_time),
        "userId": f"reporter-{uuid4().hex[:8]}",
        "contentId": content_id,
        "status": "report-accepted",
        "type": "report-accepted",
        "message": "Synthetic accepted report for sla-monitor load testing.",
        "payload": {
            "reportId": report_id,
            "postId": content_id,
            "contentId": content_id,
        },
    }
    return "report-notification", report_id, payload


def resolution_payload(
    content_id: str,
    report_id: str,
    resolution_status: str,
    event_time: datetime,
) -> tuple[str, str, dict]:
    payload = {
        "eventTime": isoformat(event_time),
        "reportId": report_id,
        "postId": content_id,
        "contentId": content_id,
        "status": resolution_status,
        "postOwnerId": f"owner-{uuid4().hex[:8]}",
    }
    return resolution_status, content_id, payload


def build_verification_events(count: int) -> list[tuple[str, str, dict]]:
    weighted_statuses = [
        ("verified", 0.55),
        ("rejected-peer", 0.2),
        ("rejected-internal", 0.1),
        ("rejected-unregistered", 0.1),
        ("timed-out", 0.05),
    ]
    statuses = random.choices(
        [item[0] for item in weighted_statuses],
        weights=[item[1] for item in weighted_statuses],
        k=count,
    )

    now = utc_now()
    events: list[tuple[str, str, dict]] = []
    for index, status in enumerate(statuses, start=1):
        event_time = now - timedelta(seconds=random.randint(0, 300))
        events.append(verification_payload(status, event_time, index))
    return events


def build_report_case_events(
    count: int,
    *,
    breach_ratio: float,
    resolved_ratio: float,
    out_of_order_ratio: float,
    max_report_age_seconds: int,
    sla_seconds: int,
) -> tuple[list[tuple[str, str, dict]], Counter[str]]:
    now = utc_now()
    events: list[tuple[str, str, dict]] = []
    counters: Counter[str] = Counter()

    for index in range(1, count + 1):
        content_id = f"load-report-content-{index}-{uuid4().hex[:6]}"
        report_id = f"load-report-{index}-{uuid4().hex[:8]}"

        breached = random.random() < breach_ratio
        resolved = random.random() < resolved_ratio
        out_of_order = resolved and random.random() < out_of_order_ratio
        resolution_status = random.choice(RESOLUTION_STATUSES)

        if breached:
            opened_at = now - timedelta(seconds=sla_seconds + random.randint(5, max(30, max_report_age_seconds)))
            counters["breached_reports"] += 1
        else:
            opened_at = now - timedelta(seconds=random.randint(0, max_report_age_seconds))
            counters["within_sla_reports"] += 1

        report_event = report_notification_payload(content_id, report_id, opened_at)
        events.append(report_event)
        counters["report_accepted_events"] += 1

        if not resolved:
            counters["open_reports"] += 1
            continue

        if breached:
            resolution_offset = random.randint(sla_seconds + 1, sla_seconds + max(60, max_report_age_seconds))
        else:
            resolution_offset = random.randint(1, max(2, min(max_report_age_seconds, sla_seconds - 1)))
        resolved_at = opened_at + timedelta(seconds=resolution_offset)
        resolution_event = resolution_payload(content_id, report_id, resolution_status, resolved_at)

        if out_of_order:
            events.append(resolution_event)
            events.append(report_event)
            counters["out_of_order_cases"] += 1
        else:
            events.append(resolution_event)

        counters["resolved_reports"] += 1
        counters[f"resolved_{resolution_status}"] += 1

    return events, counters


def maybe_pause(args: argparse.Namespace) -> None:
    if args.pause_between_batches > 0:
        time.sleep(args.pause_between_batches)


def print_json(title: str, payload: dict) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2))


def main() -> int:
    args = parse_args()
    breach_ratio = clamp_ratio(args.breach_ratio, "--breach-ratio")
    resolved_ratio = clamp_ratio(args.resolved_ratio, "--resolved-ratio")
    out_of_order_ratio = clamp_ratio(args.out_of_order_ratio, "--out-of-order-ratio")
    random.seed(args.seed)

    producer = build_producer(args.kafka_bootstrap_servers)
    produced_counts: Counter[str] = Counter()

    verification_events = build_verification_events(args.verification_events)
    report_events, report_case_counts = build_report_case_events(
        args.report_cases,
        breach_ratio=breach_ratio,
        resolved_ratio=resolved_ratio,
        out_of_order_ratio=out_of_order_ratio,
        max_report_age_seconds=args.max_report_age_seconds,
        sla_seconds=args.sla_seconds,
    )

    for topic, key, payload in verification_events:
        publish_json(producer, topic, key, payload)
        produced_counts[topic] += 1
        produced_counts[f"verification:{payload['status']}"] += 1
    producer.flush(10)
    maybe_pause(args)

    for topic, key, payload in report_events:
        publish_json(producer, topic, key, payload)
        produced_counts[topic] += 1
        status = payload.get("status")
        if status:
            produced_counts[f"{topic}:{status}"] += 1
    producer.flush(10)

    print("=== sla-monitor load generation summary ===")
    print(f"kafka_bootstrap_servers: {args.kafka_bootstrap_servers}")
    print(f"verification_events_requested: {args.verification_events}")
    print(f"report_cases_requested: {args.report_cases}")
    print(f"verification_notifications_published: {produced_counts['verification-notification']}")
    print(f"report_notifications_published: {produced_counts['report-notification']}")
    print(f"post_deleted_published: {produced_counts['post-deleted']}")
    print(f"objection_approved_published: {produced_counts['objection-approved']}")
    print(f"verified: {produced_counts['verification:verified']}")
    print(f"rejected_peer: {produced_counts['verification:rejected-peer']}")
    print(f"rejected_internal: {produced_counts['verification:rejected-internal']}")
    print(f"rejected_unregistered: {produced_counts['verification:rejected-unregistered']}")
    print(f"timed_out: {produced_counts['verification:timed-out']}")
    print(f"open_reports: {report_case_counts['open_reports']}")
    print(f"resolved_reports: {report_case_counts['resolved_reports']}")
    print(f"breached_reports: {report_case_counts['breached_reports']}")
    print(f"within_sla_reports: {report_case_counts['within_sla_reports']}")
    print(f"out_of_order_cases: {report_case_counts['out_of_order_cases']}")

    if args.skip_query:
        return 0

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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
