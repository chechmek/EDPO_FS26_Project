# EDPO Course Project

- Course: Event-driven and Process-oriented Architectures (EDPO), FS2026, University of St.Gallen
- Group 4
  - Evan Martino
  - Marco Birchler
  - Roman Babukh

## Repository

- GitHub repository: [https://github.com/chechmek/EDPO_FS26_Project](https://github.com/chechmek/EDPO_FS26_Project)

## Scope of This README

This README documents the current project setup for the Camunda-based content verification platform, including:

- complete local setup sequence
- how to start and use Camunda 8 Run (`c8run`)
- what to deploy in Camunda Modeler
- where to find and use the stream processor applications (SPA)

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
- `notification-service` (no HTTP port, Kafka consumer)

Stream processor applications (SPA):

- `sla-monitor` (port `8005`, `processors/sla-monitor`)
- `content-ledger` (port `8085`, `processors/content-ledger`)

UI:

- `ui` (port `8086`, standalone dashboard/proxy service)

Additional infra:

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
  - `check-report-valid.form`
  - `review-objection.form`
- `db/`: PostgreSQL initialization scripts (schema per service)
- `docs/`: project documentation
  - `adr/`: Architecture Decision Records
  - `exercises-submissions/`: Exercise submission documents
  - `exercises-tasksheets/`: Exercise task sheet PDFs
  - `lecture-slides/`: Lecture slide PDFs
- `misc/`: Miscellaneous files (e.g. Postman collection)
- `schemas/`: event and processor schemas (including `content-ledger` schemas)
- `processors/`: stream processor applications (`sla-monitor`, `content-ledger`)
- `scripts/`: helper scripts (`generate_load.py`, `load_sla_monitor.py`, `preview_sla_monitor.py`, `publish_content_stories.py`, `clean_stream_demo.sh`)
- `services/`: Python microservices
- `shared/`: Shared Python library used across services
- `ui/`: standalone dashboard/proxy service
- `docker-compose.infra.yml`: Kafka + Kafka UI
- `docker-compose.yml`: application services

## End-to-End Setup

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

```bash
curl -u demo:demo http://localhost:8080/v2/topology
```

- Operate: [http://localhost:8080/operate](http://localhost:8080/operate)
- Tasklist: [http://localhost:8080/tasklist](http://localhost:8080/tasklist)
- Camunda API base: [http://localhost:8080](http://localhost:8080)
- Zeebe gRPC gateway: `localhost:26500`

If API auth is disabled in your local setup:

```bash
curl http://localhost:8080/v2/topology
```

### 3) Deploy BPMN models and forms from Camunda Modeler

Deploy these BPMN files:

- `bpmn files/RegisterUser.bpmn`
- `bpmn files/VerifyContent.bpmn`
- `bpmn files/ReportContent.bpmn`

Deploy these forms:

- `bpmn files/verify-user.form`
- `bpmn files/check-report-valid.form`
- `bpmn files/review-objection.form`

What to configure in Modeler:

- Target environment: local/self-managed
- Zeebe endpoint: `localhost:26500`

Keep process IDs unchanged:

- `Process_1kwkl0j` (RegisterUser)
- `Process_01gn4xr` (VerifyContent)
- `Process_0rsygf3` (ReportContent)

Important: after pulling updates, redeploy BPMN files so Kafka connector settings in BPMN stay aligned with local Kafka (`localhost:9092`).

### 4) Start repo infrastructure (Kafka + Kafka UI)

From project root:

```bash
docker compose -f docker-compose.infra.yml up -d
```

### 5) Start application services

From project root:

```bash
docker compose up -d --build
```

Services use `ZEEBE_ADDRESS=host.docker.internal:26500`, so Camunda 8 Run must be running on the host.

### 6) Check service health

```bash
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
curl http://localhost:8004/health
curl http://localhost:8005/health
curl http://localhost:8085/api/health/stream
curl http://localhost:8086/health
```

The `notification-service` has no HTTP interface and can be monitored via its Docker logs:

```bash
docker logs cv-notification-service -f
```

## Stream Processor Applications

The SPA can be found under:

- `processors/sla-monitor`
- `processors/content-ledger`

### SLA Monitor quick usage

```bash
curl http://localhost:8005/metrics/verification
curl http://localhost:8005/sla/open-cases
curl http://localhost:8005/sla/violations
```

Preview it with live verification traffic plus synthetic moderation SLA events:

```bash
.venv/bin/python scripts/preview_sla_monitor.py
```

Generate higher-volume Kafka-only load for the processor without Camunda user tasks:

```bash
.venv/bin/python scripts/load_sla_monitor.py --verification-events 500 --report-cases 200
```

Publish curated content stories that demonstrate both the ledger and SLA monitor:

```bash
.venv/bin/python scripts/publish_content_stories.py --step-delay-seconds 2 --story-gap-seconds 1
```

Reset Kafka history and restart the minimal stream-demo stack cleanly:

```bash
./scripts/clean_stream_demo.sh
```

### Content Ledger quick usage

```bash
curl "http://localhost:8085/api/content?limit=20&withState=true"
curl "http://localhost:8085/api/content/<contentId>/state"
curl "http://localhost:8085/api/content/<contentId>/decision-trace"
```

Optional end-to-end demo:

```bash
./processors/content-ledger/scripts/demo.sh
```

Postman collection:

- `misc/content-ledger-postman-collection.json`

### For further processor-specific details

- `processors/sla-monitor/README.md`
- `processors/content-ledger/README.md`

## Process Interaction APIs (Core Services)

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

### Interact with running instances

- Verification peer verdict callback:
  - `POST http://localhost:8002/verifications/{verificationId}/peer-response`
  - triggers Camunda message correlation (`peer-approved` or `peer-rejected`)
- Report objection callback:
  - `POST http://localhost:8003/reports/{reportId}/objection`
  - triggers Camunda message correlation (`post-owner-objection`)

### Query process-facing state

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

## Localhost Interfaces and Ports

### Camunda 8 Run (non-Docker mode)

- `8080` - Camunda core (Operate, Tasklist, Identity, APIs)
- `26500` - Zeebe gRPC gateway
- `8086` - Connectors API
- `9200` - Elasticsearch
- `9300` - Elasticsearch cluster comm
- `9600` - Metrics

### Camunda web interfaces

- Operate: [http://localhost:8080/operate](http://localhost:8080/operate)
- Tasklist: [http://localhost:8080/tasklist](http://localhost:8080/tasklist)

### Project service interfaces

- `user-service`: [http://localhost:8001](http://localhost:8001)
- `verification-service`: [http://localhost:8002](http://localhost:8002)
- `reporting-service`: [http://localhost:8003](http://localhost:8003)
- `attestation-service`: [http://localhost:8004](http://localhost:8004)
- `sla-monitor`: [http://localhost:8005](http://localhost:8005)
- `content-ledger`: [http://localhost:8085](http://localhost:8085)
- `ui`: [http://localhost:8086](http://localhost:8086)
- `notification-service`: no HTTP interface (Kafka consumer only)
- Kafka UI: [http://localhost:8079](http://localhost:8079)

## Stop and Cleanup

```bash
docker compose down
docker compose -f docker-compose.infra.yml down
```

Stop Camunda 8 Run from your c8run folder:

```bash
./c8run stop
```

## Troubleshooting

- If services cannot connect to Zeebe:
  - verify c8run is running
  - verify `localhost:26500` is reachable
- If c8run fails due to port conflicts, check:
  - `8080`, `26500`, `8086`, `9200`, `9300`, `9600`
- If BPMN start fails with "process not found":
  - redeploy BPMN from Modeler
  - confirm process IDs are unchanged

