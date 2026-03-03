from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class LikeRequest(BaseModel):
    user_id: UUID


class UnlikeRequest(BaseModel):
    user_id: UUID


class CommentRequest(BaseModel):
    user_id: UUID
    text: str = Field(min_length=1, max_length=2000)


class FollowRequest(BaseModel):
    follower_id: UUID
