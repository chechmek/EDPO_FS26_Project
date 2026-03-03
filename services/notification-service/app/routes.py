from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.db import get_connection
from app.models import MarkReadResponse, NotificationResponse

router = APIRouter()


@router.get("/users/{user_id}/notifications", response_model=list[NotificationResponse])
def get_notifications(user_id: UUID, unread_only: bool = Query(default=False)) -> list[NotificationResponse]:
    base_sql = """
        SELECT id, user_id, type, message, payload, source_event_id, is_read, created_at, read_at
        FROM notifications
        WHERE user_id = %s
    """
    params: list[object] = [user_id]

    if unread_only:
        base_sql += " AND is_read = FALSE"

    base_sql += " ORDER BY created_at DESC"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(base_sql, params)
            rows = cur.fetchall()

    return [
        NotificationResponse(
            notification_id=row["id"],
            user_id=row["user_id"],
            type=row["type"],
            message=row["message"],
            payload=row["payload"],
            source_event_id=row["source_event_id"],
            is_read=row["is_read"],
            created_at=row["created_at"],
            read_at=row["read_at"],
        )
        for row in rows
    ]


@router.post(
    "/users/{user_id}/notifications/{notification_id}/read",
    response_model=MarkReadResponse,
    status_code=status.HTTP_200_OK,
)
def mark_notification_read(user_id: UUID, notification_id: UUID) -> MarkReadResponse:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE notifications
                SET is_read = TRUE,
                    read_at = NOW()
                WHERE id = %s
                  AND user_id = %s
                RETURNING id, is_read, read_at
                """,
                (notification_id, user_id),
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="notification not found",
        )

    return MarkReadResponse(
        notification_id=row["id"],
        is_read=row["is_read"],
        read_at=row["read_at"],
    )
