# Content Decision Ledger

Stream-processing application that materializes the full lifecycle of every
content item (verifications, reports, deletions, objections) into a queryable
KTable in real time.

## Tech stack


|               |                                                                                                                                             |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Language      | Java 21                                                                                                                                     |
| Stream engine | Apache Kafka Streams 3.7                                                                                                                    |
| App framework | Spring Boot 3.3                                                                                                                             |
| Build         | Maven (multi-stage Docker build, no local Java required)                                                                                    |
| Serialization | JSON (registry-less). Avro schemas provided under `[schemas/content-ledger/](../../schemas/content-ledger/)` as future-proof documentation. |
| State store   | RocksDB (default), changelog-backed                                                                                                         |
| HTTP port     | **8085**                                                                                                                                    |


## What it does (summary)

Four input topics — `verification-notification`, `report-notification`,
`post-deleted`, `objection-approved` — are normalized into a unified
`ContentEvent`, re-keyed on `contentId`, merged into one stream, and aggregated
into a `ContentDecisionState` KTable backed by RocksDB. The table is also
written to the log-compacted topic `content-decision-ledger`. Two
**Interactive Query** REST endpoints expose the materialized state for any
content item without touching any database. A small web UI on `/` lets you
browse the ledger interactively.

## Lecture concepts realized (Week 8 + Week 9)


| Concept                                         | Where it lives in the code                                     |
| ----------------------------------------------- | -------------------------------------------------------------- |
| Single-event processing (`mapValues`, `filter`) | `EventNormalizer`, `ContentLedgerTopology#readAndNormalize`    |
| `selectKey` → repartitioning                    | `ContentLedgerTopology#readAndNormalize` (`.map(...)`)         |
| Stream merging (4 → 1)                          | `ContentLedgerTopology` (`.merge(...).merge(...).merge(...)`)  |
| `groupByKey` + `aggregate` → KTable             | `ContentLedgerTopology`                                        |
| Materialized state store (RocksDB)              | `Stores.persistentKeyValueStore("content-state-store")`        |
| Streams **and** Tables in one topology          | KStream sources → KTable aggregate                             |
| Interactive Queries                             | `LedgerQueryService` + `LedgerController`                      |
| Reprocessing pattern                            | Output topic configured with `cleanup.policy=compact`          |
| Event-time hygiene                              | `PayloadEventTimeExtractor` reads `eventTime` from the payload |


## REST API


| Method | Path                                                          | Notes                                   |
| ------ | ------------------------------------------------------------- | --------------------------------------- |
| `GET`  | `/`                                                           | Web UI (static HTML/JS)                 |
| `GET`  | `/api/content?limit=&withState=`                              | Lists tracked content items             |
| `GET`  | `/api/content/{contentId}/state`                              | Current state summary (no trace)        |
| `GET`  | `/api/content/{contentId}/decision-trace`                     | **Full** chronological decision history |
| `GET`  | `/api/health/stream`                                          | Kafka Streams runtime state             |
| `GET`  | `/actuator/health` `/actuator/metrics` `/actuator/prometheus` | Spring Boot Actuator                    |


The Postman collection
`[misc/content-ledger-postman-collection.json](../../misc/content-ledger-postman-collection.json)`
covers both the query endpoints and the upstream services needed to drive
events through Camunda.

## Run

### 0. Start Camunda 8 Run (pre-requisite)

The upstream Python services drive their BPMN flows against Camunda 8 Run on
`localhost:26500`. The ledger itself does not talk to Camunda, but without it
no events will ever land on the four input topics. See the project root
[README](../../README.md) §1 for the exact `c8run` start command.

### 1. Start infra (Kafka + Kafka UI)

```bash
docker compose -f docker-compose.infra.yml up -d
```

### 2. Start the platform + the ledger

```bash
docker compose up -d --build user-service verification-service reporting-service \
                            attestation-service notification-service \
                            content-ledger
```

- ledger UI: <http://localhost:8085>

### 3. Send some traffic

Option A — convenience script (drives one full lifecycle, fully unattended):

```bash
./processors/content-ledger/scripts/demo.sh
```

The script claims and completes the two BPMN user tasks
(`Check User Background`, `Check if report is valid`) via the Camunda 8 REST
API (`/v2/user-tasks/...`) so a single invocation produces the full
4-event trace (VERIFICATION → REPORT × 2 → DELETION) without any Tasklist
interaction. Override the Camunda endpoint or credentials via the
`ZEEBE` / `ZEEBE_AUTH` env vars if needed.

Option B — Postman collection
`misc/content-ledger-postman-collection.json` (variable `content_id` is the
shared key across verification and reporting flows). User tasks have to be
completed manually in Tasklist (<http://localhost:8080/tasklist>) when using
this option.

Option C — direct curl:

```bash
curl -s http://localhost:8085/api/content | jq
curl -s http://localhost:8085/api/content/post-demo-1/state | jq
curl -s http://localhost:8085/api/content/post-demo-1/decision-trace | jq
```

## Design trade-offs (consciously chosen for this project context)

- **Single-instance Interactive Queries.** Local-store-only queries are
  sufficient at course scale; the code documents how a multi-instance setup
  would add `KafkaStreams#queryMetadataForKey`-based remote IQ.
- **JSON over Avro at runtime.** The existing Python producers emit JSON. We
  keep the same wire format here to avoid pulling in Schema Registry. The
  Avro `.avsc` definitions live in
  [`schemas/content-ledger/`](../../schemas/content-ledger/) and the
  `JsonSerde` is structured so a future swap is local to one class.
- **`contentId` correlation key.** The upstream `verification-service`
  derives `contentId = "content-<sha256(contentUrl)[:16]>"` while
  `reporting-service` / `post-deleted` / `objection-approved` use
  `contentId = postId`. For verification events and reporting events to
  land on the **same** ledger entry, the caller must use the hashed
  contentId as the postId when filing the report. The demo script and
  Postman collection do this automatically by:
    1. submitting the verification,
    2. reading back `contentId` from `GET /verifications/{id}`,
    3. using that value as the `postId` when calling `POST /reports`.
- **Uncapped decision trace.** The ledger keeps the *full* decision history
  per content item. At course scale this is bounded by the simulator's event
  volume per content item.
- **At-least-once + idempotent aggregator.** Each event carries an `eventId`;
  the aggregator deduplicates against `seenEventIds` so retries don't double-
  count decisions.
- **Out-of-order safety.** The trace is sorted by `eventTime` on insert, so
  late-arriving events end up in their chronological position rather than at
  the tail.
- **Deterministic fallbacks.** When a producer omits `eventTime` or
  `eventId`, the normalizer never reaches for `Instant.now()` or
  `UUID.randomUUID()` — that would break reprocessing determinism. The
  aggregator fills missing `eventTime` with ingestion time once, and missing
  event ids are derived from a content hash of the raw envelope.

## Tests

`mvn -f processors/content-ledger/pom.xml test` runs the topology against
`TopologyTestDriver` for: verification→report→deletion lifecycle, objection
restoring deleted content, duplicate `eventId` idempotency, out-of-order
events, and output-topic emission.

## Troubleshooting

`GET /api/content/<id>/state` returns `{"error":"not_found"}` for a `contentId`
you just submitted? The ledger is a pure consumer — `not_found` always means
no event for that key has reached the four input topics yet. Diagnose from
the ledger outwards in this order:

1. **Stream-thread is RUNNING**
   ```bash
   curl -s http://localhost:8085/api/health/stream
   ```
   Expect `{"state":"RUNNING","ready":true}`. Anything else is a ledger-side
   issue — check `docker compose logs content-ledger`.

2. **Input topics actually received the event**
   ```bash
   docker exec cv-kafka bash -lc \
     'for t in verification-notification report-notification post-deleted objection-approved; do
        echo -n "$t: "
        kafka-run-class kafka.tools.GetOffsetShell --broker-list localhost:9092 --topic "$t" | awk -F: "{print \$3}"
      done'
   ```
   If the offsets did **not** grow between two consecutive demo runs, the
   producers (`verification-service` / `reporting-service`) never published —
   the problem is upstream, not in the ledger.

3. **Upstream producers + Camunda are healthy**
   - `docker compose logs verification-service reporting-service` should show
     worker activity, not a stream of `ZeebeGatewayUnavailableError`.
   - `curl -u demo:demo http://localhost:8080/v2/topology` must return 200.
   - If only step 2 shows no growth but the services log incoming HTTP `202`s:
     `docker compose restart user-service verification-service reporting-service`
     resets stuck `pyzeebe` job pollers.

4. **`contentId` correlation is correct.** Verification publishes under the
   hashed `content-<sha256(contentUrl)[:16]>` id; reporting publishes under
   the `postId` you sent. The demo script and Postman collection thread the
   hashed id back into the report call — manual `curl` flows have to do the
   same or the two events will materialize into two different ledger
   entries.
