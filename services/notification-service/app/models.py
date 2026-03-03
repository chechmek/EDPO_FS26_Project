from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    notification_id: UUID
    user_id: UUID
    type: str
    message: str
    payload: dict
    source_event_id: UUID
    is_read: bool
    created_at: datetime
    read_at: datetime | None


class MarkReadResponse(BaseModel):
    notification_id: UUID
    is_read: bool
    read_at: datetime | None
