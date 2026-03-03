# EDPO Course Project

Social media simulation for **Event Driven and Process Oriented Systems** using Python 3.12, Kafka, and PostgreSQL.

It demonstrates the **Event Notification** pattern with three services:
- `post-service`: stores posts and emits `PostCreated`
- `interaction-service`: stores likes/comments/follows and emits interaction events
- `notification-service`: consumes interaction events and builds notifications in its own datastore

## Exercise 2

This draft covers the **Event Notification** EDA pattern.

Implemented mapping to the pattern:
- `interaction-service` publishes interaction events (`PostLiked`, `PostUnliked`, `CommentAdded`, `UserFollowed`) to Kafka.
- `notification-service` subscribes to events and creates owner-facing notifications only for `PostLiked` and `CommentAdded` (`Your post was liked`, `Your post was commented`).
- There is **no direct synchronous call** from `interaction-service` to `notification-service` (dependency inversion / decoupling).


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

## Architecture

### Event Flow

1. Client calls `interaction-service` or `post-service` HTTP APIs.
2. `interaction-service` writes domain state and publishes to Kafka topic `social.interaction.events`.
3. `notification-service` consumes from `social.post.events` and `social.interaction.events` and creates notifications.
4. Client reads notifications through `notification-service` API. (Also printed in console)

`post-service` independently emits `PostCreated` to `social.post.events`.

### Events

All services use `shared/events.py`:

```json
{
  "event_id": "7f6e43d8-9187-4df1-a7e2-e5e08c1e6c9b",
  "type": "PostLiked",
  "ts": "2026-03-03T12:00:00Z",
  "payload": {}
}
```

Event types currently defined in `shared/events.py`:

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

No service writes directly to another service's DB.

## Local Setup (venv + uv)

### Prerequisites

- Docker + Docker Compose
- Python 3.12
- Make

### Create virtual environment

```bash
make venv
```

### Install dependencies with uv

```bash
make install
```

Equivalent command:

```bash
.venv/bin/uv pip install -e ./shared -e ./services/post-service -e ./services/interaction-service -e ./services/notification-service requests
```

## Run the System

### Start infrastructure + services

```bash
make up
```

Services:
- Post API: `http://localhost:8001`
- Interaction API: `http://localhost:8002`
- Notification API: `http://localhost:8003`
- Kafka UI: `http://localhost:8080`
- Kafka bootstrap (host): `localhost:19092`
- Postgres (host): `localhost:15432`

### Stop

```bash
make down
```

## Demo / Event Generation

Primary event generator:

```bash
.venv/bin/python scripts/generate_load.py --posts 10 --users 20 --events 200 --target-user 11111111-1111-4111-8111-111111111111
```

Defaults generate **200 interaction requests** (minimum enforced: 100), creating events indirectly through HTTP APIs.

Quick demo script:

```bash
./scripts/demo.sh
```

or:

```bash
make demo
```

## API Endpoints

All exposed HTTP endpoints by service:

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

Notification types created by `notification-service`:
- `PostLiked` with message `Your post was liked`
- `CommentAdded` with message `Your post was commented`

`PostUnliked` and `UserFollowed` events are consumed but do not create notifications.

## Observability

### Kafka UI

- URL: `http://localhost:8080`
- Open cluster `local`
- Browse topics and messages:
  - `social.post.events`
  - `social.interaction.events`


