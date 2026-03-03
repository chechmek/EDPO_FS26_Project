from __future__ import annotations

from uuid import UUID

from confluent_kafka import Producer

from shared.events import (
    CommentAddedPayload,
    PostLikedPayload,
    PostUnlikedPayload,
    UserFollowedPayload,
    create_envelope,
)
from shared.kafka import publish_event


class InteractionEventPublisher:
    def __init__(self, producer: Producer, topic: str) -> None:
        self._producer = producer
        self._topic = topic

    def publish_post_liked(self, *, post_id: UUID, user_id: UUID) -> None:
        envelope = create_envelope("PostLiked", PostLikedPayload(post_id=post_id, user_id=user_id))
        publish_event(self._producer, topic=self._topic, key=str(post_id), envelope=envelope)

    def publish_post_unliked(self, *, post_id: UUID, user_id: UUID) -> None:
        envelope = create_envelope("PostUnliked", PostUnlikedPayload(post_id=post_id, user_id=user_id))
        publish_event(self._producer, topic=self._topic, key=str(post_id), envelope=envelope)

    def publish_comment_added(self, *, comment_id: UUID, post_id: UUID, user_id: UUID, text: str) -> None:
        envelope = create_envelope(
            "CommentAdded",
            CommentAddedPayload(comment_id=comment_id, post_id=post_id, user_id=user_id, text=text),
        )
        publish_event(self._producer, topic=self._topic, key=str(post_id), envelope=envelope)

    def publish_user_followed(self, *, follower_id: UUID, followee_id: UUID) -> None:
        envelope = create_envelope(
            "UserFollowed",
            UserFollowedPayload(follower_id=follower_id, followee_id=followee_id),
        )
        publish_event(self._producer, topic=self._topic, key=str(followee_id), envelope=envelope)
