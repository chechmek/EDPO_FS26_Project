# EDPO Course Project

Social media simulation for **Event Driven and Process Oriented Systems** using Python 3.12, Kafka, and PostgreSQL.

It demonstrates the **Event Notification** pattern with three services:
- `post-service`: stores posts and emits `PostCreated`
- `interaction-service`: stores likes/comments/follows and emits interaction events
- `notification-service`: consumes interaction events and builds notifications in its own datastore

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

### post-service

- `POST /posts` body: `{ "author_id": "cb3fc3df-7e44-4b87-b5de-8fcd46491290", "content": "hello" }`
- `GET /posts/{post_id}`

### interaction-service

- `POST /posts/{post_id}/like` body: `{ "user_id": "cb3fc3df-7e44-4b87-b5de-8fcd46491290" }`
- `POST /posts/{post_id}/unlike` body: `{ "user_id": "cb3fc3df-7e44-4b87-b5de-8fcd46491290" }`
- `POST /posts/{post_id}/comment` body: `{ "user_id": "cb3fc3df-7e44-4b87-b5de-8fcd46491290", "text": "nice" }`
- `POST /users/{followee_id}/follow` body: `{ "follower_id": "4c43a16f-670c-401a-a257-f6cca0536965" }`

Partition key strategy:
- Post-related events: key = `post_id`
- Follow events: key = `followee_id`

### notification-service

- `GET /users/{user_id}/notifications?unread_only=false`
- `POST /users/{user_id}/notifications/{notification_id}/read`

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

### Console consumer examples

Read interaction topic from inside Kafka container:

```bash
docker exec -it edpo-kafka kafka-console-consumer \
  --bootstrap-server kafka:29092 \
  --topic social.interaction.events \
  --from-beginning
```

Read post topic:

```bash
docker exec -it edpo-kafka kafka-console-consumer \
  --bootstrap-server kafka:29092 \
  --topic social.post.events \
  --from-beginning
```

### Query Postgres tables

```bash
docker exec -it edpo-postgres psql -U postgres -d post_db -c "SELECT id,author_id,created_at FROM posts ORDER BY created_at DESC LIMIT 5;"
docker exec -it edpo-postgres psql -U postgres -d interaction_db -c "SELECT id,post_id,user_id,created_at FROM comments ORDER BY created_at DESC LIMIT 5;"
docker exec -it edpo-postgres psql -U postgres -d notification_db -c "SELECT id,user_id,type,source_event_id,is_read FROM notifications ORDER BY created_at DESC LIMIT 10;"
```

## Running Services Locally with uv (without Docker for apps)

Infrastructure still via Docker (`kafka`, `kafka-ui`, `postgres`), then run services from source:

```bash
source .venv/bin/activate

# terminal 1
cd services/post-service
DB_DSN=postgresql://post_user:post_pass@localhost:15432/post_db \
KAFKA_BOOTSTRAP_SERVERS=localhost:19092 \
uv run --no-project uvicorn app.main:app --reload --port 8001

# terminal 2
cd services/interaction-service
DB_DSN=postgresql://interaction_user:interaction_pass@localhost:15432/interaction_db \
KAFKA_BOOTSTRAP_SERVERS=localhost:19092 \
uv run --no-project uvicorn app.main:app --reload --port 8002

# terminal 3
cd services/notification-service
DB_DSN=postgresql://notification_user:notification_pass@localhost:15432/notification_db \
KAFKA_BOOTSTRAP_SERVERS=localhost:19092 \
KAFKA_GROUP_ID=notification-service \
uv run --no-project uvicorn app.main:app --reload --port 8003
```

## Exercise 2

This draft covers the **Event Notification** EDA pattern (from the lecture’s **four common patterns**).

Implemented mapping to the pattern:
- `interaction-service` publishes interaction events (`PostLiked`, `PostUnliked`, `CommentAdded`, `UserFollowed`) to Kafka.
- `notification-service` subscribes to events and creates owner-facing notifications only for `PostLiked` and `CommentAdded` (`Your post was liked`, `Your post was commented`).
- There is **no direct synchronous call** from `interaction-service` to `notification-service` (dependency inversion / decoupling).

How to observe it:
- Kafka UI shows produced interaction events.
- `scripts/generate_load.py` generates high-volume interactions via HTTP, which trigger Kafka events and notification creation.
