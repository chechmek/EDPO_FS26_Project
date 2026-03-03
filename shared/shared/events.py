from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    type: str
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any]


class PostCreatedPayload(BaseModel):
    post_id: UUID
    author_id: UUID


class PostLikedPayload(BaseModel):
    post_id: UUID
    user_id: UUID


class PostUnlikedPayload(BaseModel):
    post_id: UUID
    user_id: UUID


class CommentAddedPayload(BaseModel):
    comment_id: UUID
    post_id: UUID
    user_id: UUID
    text: str


class UserFollowedPayload(BaseModel):
    follower_id: UUID
    followee_id: UUID


PayloadModel = (
    PostCreatedPayload
    | PostLikedPayload
    | PostUnlikedPayload
    | CommentAddedPayload
    | UserFollowedPayload
)


PAYLOAD_BY_TYPE: dict[str, type[BaseModel]] = {
    "PostCreated": PostCreatedPayload,
    "PostLiked": PostLikedPayload,
    "PostUnliked": PostUnlikedPayload,
    "CommentAdded": CommentAddedPayload,
    "UserFollowed": UserFollowedPayload,
}


def create_envelope(
    event_type: str,
    payload: BaseModel,
    *,
    event_id: UUID | None = None,
    ts: datetime | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id or uuid4(),
        type=event_type,
        ts=ts or datetime.now(timezone.utc),
        payload=payload.model_dump(mode="json"),
    )


def parse_typed_event(raw: dict[str, Any]) -> tuple[EventEnvelope, PayloadModel]:
    envelope = EventEnvelope.model_validate(raw)
    payload_model = PAYLOAD_BY_TYPE.get(envelope.type)
    if payload_model is None:
        raise ValueError(f"unsupported event type: {envelope.type}")
    payload = payload_model.model_validate(envelope.payload)
    return envelope, payload
