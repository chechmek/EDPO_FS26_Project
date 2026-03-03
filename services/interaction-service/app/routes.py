from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.db import get_connection
from app.models import CommentRequest, FollowRequest, LikeRequest, UnlikeRequest

router = APIRouter()


@router.post("/posts/{post_id}/like", status_code=status.HTTP_200_OK)
def like_post(post_id: UUID, payload: LikeRequest, request: Request) -> dict[str, object]:
    inserted = False
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO likes (post_id, user_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                RETURNING post_id
                """,
                (post_id, payload.user_id),
            )
            inserted = cur.fetchone() is not None
        conn.commit()

    if inserted:
        request.app.state.publisher.publish_post_liked(post_id=post_id, user_id=payload.user_id)

    return {"status": "liked", "changed": inserted}


@router.post("/posts/{post_id}/unlike", status_code=status.HTTP_200_OK)
def unlike_post(post_id: UUID, payload: UnlikeRequest, request: Request) -> dict[str, object]:
    deleted = False
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM likes
                WHERE post_id = %s AND user_id = %s
                RETURNING post_id
                """,
                (post_id, payload.user_id),
            )
            deleted = cur.fetchone() is not None
        conn.commit()

    if deleted:
        request.app.state.publisher.publish_post_unliked(post_id=post_id, user_id=payload.user_id)

    return {"status": "unliked", "changed": deleted}


@router.post("/posts/{post_id}/comment", status_code=status.HTTP_201_CREATED)
def comment_post(post_id: UUID, payload: CommentRequest, request: Request) -> dict[str, object]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO comments (post_id, user_id, text)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (post_id, payload.user_id, payload.text),
            )
            row = cur.fetchone()
        conn.commit()

    comment_id = row["id"]
    request.app.state.publisher.publish_comment_added(
        comment_id=comment_id,
        post_id=post_id,
        user_id=payload.user_id,
        text=payload.text,
    )

    return {"status": "commented", "comment_id": comment_id}


@router.post("/users/{followee_id}/follow", status_code=status.HTTP_200_OK)
def follow_user(followee_id: UUID, payload: FollowRequest, request: Request) -> dict[str, object]:
    inserted = False
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO follows (follower_id, followee_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                RETURNING followee_id
                """,
                (payload.follower_id, followee_id),
            )
            inserted = cur.fetchone() is not None
        conn.commit()

    if inserted:
        request.app.state.publisher.publish_user_followed(
            follower_id=payload.follower_id,
            followee_id=followee_id,
        )

    return {"status": "followed", "changed": inserted}
