# EDPO Course Project

- Course: Event-driven and Process-oriented Architectures (EDPO), FS2026, University of St.Gallen
- Group 4
  - Evan Martino
  - Marco Birchler
  - Roman Babukh

## Repository

- GitHub repository: <https://github.com/chechmek/EDPO_FS26_Project>

## Repository Purpose

This repository contains the implementation of our project and documentation for each exercise starting from exercise 2. The project demonstrates an event-driven social media simulation using the **Event Notification** EDA pattern. Three independent services communicate asynchronously through Kafka — no direct service-to-service calls.

## Project Structure

- `services/post-service`: stores posts and emits `PostCreated`
- `services/interaction-service`: stores likes/comments/follows and emits interaction events
- `services/notification-service`: consumes events and creates owner-facing notifications
- `shared`: shared event schemas and utilities
- `scripts`: demo and load-generation scripts
- `db/init`: database initialization scripts

For the detailed Exercise 2 hand-in document, see [`exercises-submissions/submission-exercise-2.md`](exercises-submissions/submission-exercise-2.md).

## Tech Stack

- Python 3.12
- FastAPI + Uvicorn
- `uv` for dependency management and running
- `venv` for local environment isolation
- Pydantic v2 + pydantic-settings
- Kafka (KRaft single node)
- Kafka UI
- PostgreSQL (single instance, separate DB per service)
- Docker Compose

## Architecture Overview

```
Client
  │
  ├─► POST /posts          → post-service       → Kafka: social.post.events
  │                                                         │
  └─► POST /posts/{id}/like → interaction-service → Kafka: social.interaction.events
                                                         │
                                                  notification-service
                                                  (consumes both topics)
                                                         │
                                                  GET /users/{id}/notifications
```

Services own their own databases — no cross-service DB writes.

## Events

All services use `shared/events.py` with a common envelope:

```json
{
  "event_id": "7f6e43d8-9187-4df1-a7e2-e5e08c1e6c9b",
  "type": "PostLiked",
  "ts": "2026-03-03T12:00:00Z",
  "payload": {}
}
```

Defined event types:
- `PostCreated`
- `PostLiked`
- `PostUnliked`
- `CommentAdded`
- `UserFollowed`

Notification behavior:
- `notification-service` consumes `PostCreated` to maintain `post_owners`
- `notification-service` creates notifications only for `PostLiked` and `CommentAdded`
- `PostUnliked` and `UserFollowed` are consumed but do not create notifications

## Data Models

### Event Models

Envelope:
- `event_id: UUID`
- `type: str`
- `ts: datetime (UTC ISO8601)`
- `payload: object`

Payloads:
- `PostCreatedPayload`
  - `post_id: UUID`
  - `author_id: UUID`
- `PostLikedPayload`
  - `post_id: UUID`
  - `user_id: UUID`
- `PostUnlikedPayload`
  - `post_id: UUID`
  - `user_id: UUID`
- `CommentAddedPayload`
  - `comment_id: UUID`
  - `post_id: UUID`
  - `user_id: UUID`
  - `text: str`
- `UserFollowedPayload`
  - `follower_id: UUID`
  - `followee_id: UUID`

### Persistence Models

`post_db.posts`
- `id: UUID`
- `author_id: UUID`
- `content: TEXT`
- `created_at: TIMESTAMPTZ`

`interaction_db.likes`
- `post_id: UUID`
- `user_id: UUID`
- `created_at: TIMESTAMPTZ`

`interaction_db.comments`
- `id: UUID`
- `post_id: UUID`
- `user_id: UUID`
- `text: TEXT`
- `created_at: TIMESTAMPTZ`

`interaction_db.follows`
- `follower_id: UUID`
- `followee_id: UUID`
- `created_at: TIMESTAMPTZ`

`notification_db.post_owners`
- `post_id: UUID`
- `author_id: UUID`
- `updated_at: TIMESTAMPTZ`

`notification_db.notifications`
- `id: UUID`
- `user_id: UUID`
- `type: TEXT`
- `message: TEXT`
- `payload: JSONB`
- `source_event_id: UUID (UNIQUE)`
- `is_read: BOOLEAN`
- `created_at: TIMESTAMPTZ`
- `read_at: TIMESTAMPTZ | NULL`

### Topics

- `social.post.events`
- `social.interaction.events`

## Data Ownership

Each service owns its own Postgres database and tables:
- `post_db` (owner: `post_user`): `posts`
- `interaction_db` (owner: `interaction_user`): `likes`, `comments`, `follows`
- `notification_db` (owner: `notification_user`): `notifications`, `post_owners`

No service writes directly to another service's database.

## Quick Start

### Prerequisites

- Docker + Docker Compose
- Python 3.12
- Make

### Setup

```bash
make venv
make install
```

### Run

```bash
make up
```

### Stop

```bash
make down
```

## Service Endpoints

- Post API: `http://localhost:8001`
- Interaction API: `http://localhost:8002`
- Notification API: `http://localhost:8003`
- Kafka UI: `http://localhost:8080`

## API Endpoints (Details)

`post-service` (`http://localhost:8001`)
- `POST /posts` body: `{ "author_id": "<uuid>", "content": "hello" }`
- `GET /posts/{post_id}`
- `GET /docs`
- `GET /redoc`
- `GET /openapi.json`

`interaction-service` (`http://localhost:8002`)
- `POST /posts/{post_id}/like` body: `{ "user_id": "<uuid>" }`
- `POST /posts/{post_id}/unlike` body: `{ "user_id": "<uuid>" }`
- `POST /posts/{post_id}/comment` body: `{ "user_id": "<uuid>", "text": "nice" }`
- `POST /users/{followee_id}/follow` body: `{ "follower_id": "<uuid>" }`
- `GET /docs`
- `GET /redoc`
- `GET /openapi.json`

Partition key strategy:
- Post-related events: key = `post_id`
- Follow events: key = `followee_id`

`notification-service` (`http://localhost:8003`)
- `GET /users/{user_id}/notifications?unread_only=false`
- `POST /users/{user_id}/notifications/{notification_id}/read`
- `GET /docs`
- `GET /redoc`
- `GET /openapi.json`

## Observability

### Kafka UI

- URL: `http://localhost:8080`
- Open cluster `local`
- Browse topics and messages:
  - `social.post.events`
  - `social.interaction.events`


