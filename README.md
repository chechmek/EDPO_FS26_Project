# EDPO Course Project

- Course: Event-driven and Process-oriented Architectures (EDPO), FS2026, University of St.Gallen
- Group 4
  - Evan Martino
  - Marco Birchler
  - Roman Babukh

## Repository

- GitHub repository: <https://github.com/chechmek/EDPO_FS26_Project>

## Scope of This README

This README documents the current project setup for the Camunda-based content verification platform, including:

- complete local setup sequence
- how to start and use Camunda 8 Run (`c8run`)
- what to do in Camunda Modeler
- Camunda interfaces and localhost ports
- REST endpoints to start and interact with process instances

## Project Overview

This project implements three orchestrated BPMN processes on Camunda 8 (Zeebe):

- `RegisterUser.bpmn` (`Process_1kwkl0j`)
- `VerifyContent.bpmn` (`Process_01gn4xr`)
- `ReportContent.bpmn` (`Process_0rsygf3`)

Python services expose REST APIs and run Zeebe workers to execute BPMN service tasks:

- `user-service` (port `8001`)
- `verification-service` (port `8002`)
- `reporting-service` (port `8003`)
- `attestation-service` (port `8004`, not orchestrated by Camunda)

Additional infra in this repo:

- Kafka (`9092`)
- Kafka UI (`8079`)

## Prerequisites

- Docker + Docker Compose
- Python 3.12 (for optional local scripts)
- Java 21-23 (required for non-Docker Camunda 8 Run mode)
- Camunda Modeler Desktop (to open and deploy BPMN/form files)

## Folder Guide

- `bpmn files/`: BPMN and form files to deploy
  - `RegisterUser.bpmn`
  - `VerifyContent.bpmn`
  - `ReportContent.bpmn`
  - `verify-user.form`
- `services/`: Python microservices (Flask/FastAPI)
- `docker-compose.infra.yml`: Kafka + Kafka UI
- `docker-compose.yml`: application services

## End-to-End Setup (Recommended Order)

### 1) Start Camunda 8 Run (`c8run`)

Use your Camunda 8 Run folder (example path below):

```bash
cd /Users/Marco/Downloads/c8run-8.8.11
./start.sh
```

Alternative:

```bash
cd /Users/Marco/Downloads/c8run-8.8.11
./c8run start
```

If you prefer Docker mode in c8run:

```bash
./start.sh --docker
```

### 2) Verify Camunda 8 Run is reachable

In standard (non-Docker) c8run mode:

- Operate: <http://localhost:8080/operate>
- Tasklist: <http://localhost:8080/tasklist>
- Camunda API base: <http://localhost:8080>
- Zeebe gRPC gateway: `localhost:26500`

Useful quick check:

```bash
curl http://localhost:8080/v2/topology
```

If API auth is enabled, use credentials (for example `demo:demo`):

```bash
curl -u demo:demo http://localhost:8080/v2/topology
```

In c8run Docker mode, Operate is typically available at:

- <http://localhost:8088/operate>

### 3) Deploy BPMN models from Camunda Modeler

Open each BPMN file from `bpmn files/` in Camunda Modeler and deploy it to your local Zeebe cluster.

Important: redeploy the BPMN files after pulling the latest repo changes. The Kafka connector tasks in
`RegisterUser.bpmn` and `ReportContent.bpmn` now target `localhost:9092`, which is required when Camunda 8 Run is
running on your host machine and Kafka is exposed from Docker on port `9092`.

#### What to configure in Modeler

- Target environment: local/self-managed
- Zeebe endpoint: `localhost:26500`
- Deploy these files:
  - `bpmn files/RegisterUser.bpmn`
  - `bpmn files/VerifyContent.bpmn`
  - `bpmn files/ReportContent.bpmn`
- Also deploy form `bpmn files/verify-user.form` (form id `verify-user`)

Important: keep the process IDs unchanged because services start processes by these IDs:

- `Process_1kwkl0j` (RegisterUser)
- `Process_01gn4xr` (VerifyContent)
- `Process_0rsygf3` (ReportContent)

### 4) Start repo infrastructure (Kafka + Kafka UI)

From project root:

```bash
docker compose -f docker-compose.infra.yml up -d
```

This also creates the `cv-infra` network used by application services.

### 5) Start application services

From project root:

```bash
docker compose up -d --build
```

Services use:

- `ZEEBE_ADDRESS=host.docker.internal:26500`

So Camunda 8 Run must be running on your host before starting/using these services.

### 6) Check service health

```bash
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
curl http://localhost:8004/health
curl http://localhost:8005/health
```

## Localhost Interfaces and Ports

### Camunda 8 Run (non-Docker mode)

- `8080` - Camunda core (Operate, Tasklist, Identity, APIs)
- `26500` - Zeebe gRPC gateway
- `8086` - Connectors API
- `9200` - Elasticsearch
- `9300` - Elasticsearch cluster comm
- `9600` - Metrics

### Camunda web interfaces

- Operate: <http://localhost:8080/operate>
- Tasklist: <http://localhost:8080/tasklist>

### Project service interfaces

- `user-service`: <http://localhost:8001>
- `verification-service`: <http://localhost:8002>
- `reporting-service`: <http://localhost:8003>
- `attestation-service`: <http://localhost:8004>
- Kafka UI: <http://localhost:8079>

## Process Interaction APIs (Start and Drive Instances)

### Start process instances

- Register user process:
  - `POST http://localhost:8001/users`
  - body: `{ "username": "alice", "password": "secret" }`
- Verify content process:
  - `POST http://localhost:8002/verifications`
  - body: `{ "userId": "<user-id>", "contentUrl": "https://example.com", "contentTitle": "Example", "peerMode": "manual" }`
- Report content process:
  - `POST http://localhost:8003/reports`
  - body: `{ "reporterId": "<user-id>", "postId": "post-123", "postOwnerId": "<owner-id>", "reason": "spam", "objectionMode": "manual" }`

### Interact with running instances (message correlation paths)

- Verification peer verdict callback:
  - `POST http://localhost:8002/verifications/{verificationId}/peer-response`
  - triggers Camunda message correlation (`peer-approved` or `peer-rejected`)
- Report objection callback:
  - `POST http://localhost:8003/reports/{reportId}/objection`
  - triggers Camunda message correlation (`post-owner-objection`)

### Query local process-facing state

- `GET http://localhost:8002/verifications/{verificationId}`
- `GET http://localhost:8003/reports/{reportId}`

## Minimal Test Flow (Copy/Paste)

```bash
# 1) Start RegisterUser process
curl -X POST http://localhost:8001/users \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"secret"}'

# 2) Start VerifyContent process (replace userId with the approved userId)
curl -X POST http://localhost:8002/verifications \
  -H "Content-Type: application/json" \
  -d '{"userId":"11111111-1111-4111-8111-111111111111","contentUrl":"https://example.com","contentTitle":"Example","peerMode":"manual"}'

# 3) Simulate peer verdict (replace verificationId)
curl -X POST http://localhost:8002/verifications/<verificationId>/peer-response \
  -H "Content-Type: application/json" \
  -d '{"peerId":"peer-1","approved":true}'

# 4) Start ReportContent process
curl -X POST http://localhost:8003/reports \
  -H "Content-Type: application/json" \
  -d '{"reporterId":"11111111-1111-4111-8111-111111111111","postId":"post-123","postOwnerId":"22222222-2222-4222-8222-222222222222","reason":"spam","objectionMode":"manual"}'
```

Watch the process in Operate while executing these calls.

## Stop and Cleanup

### Stop project containers

```bash
docker compose down
docker compose -f docker-compose.infra.yml down
```

### Stop Camunda 8 Run

From your c8run directory:

```bash
./c8run stop
```

If needed:

```bash
./shutdown.sh
```

## Troubleshooting

- If services cannot connect to Zeebe:
  - verify c8run is running
  - verify `localhost:26500` is open
- If c8run fails to start due to port conflicts, check:
  - `8080`, `26500`, `8086`, `9200`, `9300`, `9600`
- If BPMN start fails with "process not found":
  - redeploy BPMN from Modeler
  - confirm process IDs are unchanged

