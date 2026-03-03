#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
import string
import time
from collections import Counter
from uuid import UUID, uuid4

import requests


def random_text(length: int = 32) -> str:
    letters = string.ascii_lowercase + " "
    return "".join(random.choice(letters) for _ in range(length)).strip() or "sample text"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate load for EDPO social media simulation")
    parser.add_argument("--post-service-url", default="http://localhost:8001")
    parser.add_argument("--interaction-service-url", default="http://localhost:8002")
    parser.add_argument("--notification-service-url", default="http://localhost:8003")
    parser.add_argument("--posts", type=int, default=10)
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--events", type=int, default=200)
    parser.add_argument("--target-user", type=UUID, default=None)
    parser.add_argument("--sleep", type=float, default=3.0, help="Seconds to wait for consumer processing")
    return parser.parse_args()


def create_posts(post_service_url: str, users: list[UUID], post_count: int, target_user: UUID) -> tuple[list[UUID], list[UUID]]:
    post_ids: list[UUID] = []
    target_post_ids: list[UUID] = []
    for _ in range(post_count):
        author_id = random.choice(users)
        # Ensure at least one post belongs to the target user for deterministic owner-notification demos.
        if not target_post_ids:
            author_id = target_user
        payload = {
            "author_id": str(author_id),
            "content": f"post {random_text(40)}",
        }
        response = requests.post(f"{post_service_url}/posts", json=payload, timeout=10)
        response.raise_for_status()
        post_id = UUID(response.json()["post_id"])
        post_ids.append(post_id)
        if author_id == target_user:
            target_post_ids.append(post_id)
    return post_ids, target_post_ids


def build_user_pool(count: int, target_user: UUID) -> list[UUID]:
    users: list[UUID] = [target_user]
    seen: set[UUID] = {target_user}
    while len(users) < count:
        candidate = uuid4()
        if candidate in seen:
            continue
        seen.add(candidate)
        users.append(candidate)
    return users


def main() -> int:
    args = parse_args()

    if args.events < 100:
        raise SystemExit("--events must be >= 100 to satisfy project requirements")
    if args.users < 2:
        raise SystemExit("--users must be >= 2")

    random.seed(42)

    target_user = args.target_user or uuid4()
    users = build_user_pool(args.users, target_user)
    non_target_users = [user for user in users if user != target_user]
    post_ids, target_post_ids = create_posts(args.post_service_url, users, args.posts, target_user)

    action_counter: Counter[str] = Counter()
    failures = 0

    events_sent = 0
    attempts = 0
    max_attempts = args.events * 10

    while events_sent < args.events and attempts < max_attempts:
        attempts += 1
        action = random.choices(
            ["like", "unlike", "comment", "follow"],
            weights=[0.4, 0.2, 0.25, 0.15],
            k=1,
        )[0]

        user_id = random.choice(users)

        try:
            if action == "like":
                post_id = random.choice(target_post_ids) if target_post_ids and random.random() < 0.35 else random.choice(post_ids)
                if post_id in target_post_ids:
                    user_id = random.choice(non_target_users)
                requests.post(
                    f"{args.interaction_service_url}/posts/{post_id}/like",
                    json={"user_id": str(user_id)},
                    timeout=10,
                ).raise_for_status()

            elif action == "unlike":
                post_id = random.choice(post_ids)
                requests.post(
                    f"{args.interaction_service_url}/posts/{post_id}/unlike",
                    json={"user_id": str(user_id)},
                    timeout=10,
                ).raise_for_status()

            elif action == "comment":
                post_id = random.choice(target_post_ids) if target_post_ids and random.random() < 0.35 else random.choice(post_ids)
                if post_id in target_post_ids:
                    user_id = random.choice(non_target_users)
                requests.post(
                    f"{args.interaction_service_url}/posts/{post_id}/comment",
                    json={"user_id": str(user_id), "text": f"comment {random_text(24)}"},
                    timeout=10,
                ).raise_for_status()

            elif action == "follow":
                followee = random.choice(users)
                if followee == user_id:
                    continue
                requests.post(
                    f"{args.interaction_service_url}/users/{followee}/follow",
                    json={"follower_id": str(user_id)},
                    timeout=10,
                ).raise_for_status()

            action_counter[action] += 1
            events_sent += 1
        except requests.RequestException:
            failures += 1

    if events_sent < args.events:
        raise SystemExit(
            f"Unable to send requested events: requested={args.events}, sent={events_sent}, attempts={attempts}"
        )

    time.sleep(args.sleep)

    notifications_response = requests.get(
        f"{args.notification_service_url}/users/{target_user}/notifications",
        timeout=10,
    )
    notifications_response.raise_for_status()
    notifications = notifications_response.json()

    print("=== Load Generation Summary ===")
    print(f"target_user: {target_user}")
    print(f"posts_created: {len(post_ids)}")
    print(f"events_requested: {args.events}")
    print(f"events_sent: {events_sent}")
    print(f"failures: {failures}")
    print(f"likes: {action_counter['like']}")
    print(f"unlikes: {action_counter['unlike']}")
    print(f"comments: {action_counter['comment']}")
    print(f"follows: {action_counter['follow']}")
    print()
    print(f"notifications_for_{target_user}: {len(notifications)}")
    print("sample_notifications:")
    for item in notifications[:5]:
        print(item)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
