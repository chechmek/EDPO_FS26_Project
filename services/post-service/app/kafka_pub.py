from __future__ import annotations

from uuid import UUID

from confluent_kafka import Producer

from shared.events import PostCreatedPayload, create_envelope
from shared.kafka import publish_event


class PostEventPublisher:
    def __init__(self, producer: Producer, topic: str) -> None:
        self._producer = producer
        self._topic = topic

    def publish_post_created(self, *, post_id: UUID, author_id: UUID) -> None:
        envelope = create_envelope(
            "PostCreated",
            PostCreatedPayload(post_id=post_id, author_id=author_id),
        )
        publish_event(
            self._producer,
            topic=self._topic,
            key=str(post_id),
            envelope=envelope,
        )
