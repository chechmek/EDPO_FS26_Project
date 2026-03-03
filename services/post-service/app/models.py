from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CreatePostRequest(BaseModel):
    author_id: UUID
    content: str = Field(min_length=1, max_length=5000)


class CreatePostResponse(BaseModel):
    post_id: UUID


class PostResponse(BaseModel):
    post_id: UUID
    author_id: UUID
    content: str
    created_at: datetime
