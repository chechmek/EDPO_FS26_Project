from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from app.db import get_connection
from app.models import CreatePostRequest, CreatePostResponse, PostResponse

router = APIRouter()


@router.post("/posts", response_model=CreatePostResponse, status_code=status.HTTP_201_CREATED)
def create_post(payload: CreatePostRequest, request: Request) -> CreatePostResponse:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO posts (author_id, content)
                VALUES (%s, %s)
                RETURNING id
                """,
                (payload.author_id, payload.content),
            )
            row = cur.fetchone()
        conn.commit()

    post_id = row["id"]
    request.app.state.publisher.publish_post_created(
        post_id=post_id,
        author_id=payload.author_id,
    )

    return CreatePostResponse(post_id=post_id)


@router.get("/posts/{post_id}", response_model=PostResponse)
def get_post(post_id: UUID) -> PostResponse:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, author_id, content, created_at
                FROM posts
                WHERE id = %s
                """,
                (post_id,),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="post not found")

    return PostResponse(
        post_id=row["id"],
        author_id=row["author_id"],
        content=row["content"],
        created_at=row["created_at"],
    )
