# Content Ledger

Java Kafka Streams processor that builds a queryable decision history per `contentId`.

## What It Does

- Consumes: `verification-notification`, `report-notification`, `post-deleted`, `objection-approved`
- Builds materialized state in Kafka Streams (RocksDB store)
- Serves query APIs on port `8085`
- Writes aggregated topic: `content-decision-ledger`

The topology reads four domain streams, normalizes them into one `ContentEvent` model, re-keys everything by `contentId`, and merges the streams. It then applies `groupByKey + aggregate` to build one `ContentDecisionState` per content item in a materialized Kafka Streams state store. This state is exposed via Interactive Query endpoints and also emitted to the compacted `content-decision-ledger` topic.

## Lecture Concepts Realized


| Concept                                           | Where to find it in code                                                                                                                                                                                           |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Stateless operations (`mapValues`, `filter`)      | `processors/content-ledger/src/main/java/com/edpo/contentledger/stream/ContentLedgerTopology.java` (`readAndNormalize`)                                                                                            |
| Re-keying / repartitioning (`selectKey` concept)  | `processors/content-ledger/src/main/java/com/edpo/contentledger/stream/ContentLedgerTopology.java` (`readAndNormalize` -> `.map(... event.contentId())`)                                                           |
| Multi-stream merge (four topics into one flow)    | `processors/content-ledger/src/main/java/com/edpo/contentledger/stream/ContentLedgerTopology.java` (`verifications.merge(...).merge(...).merge(...)`)                                                              |
| Streams + Tables together (`KStream` -> `KTable`) | `processors/content-ledger/src/main/java/com/edpo/contentledger/stream/ContentLedgerTopology.java` (`groupByKey().aggregate(...)`)                                                                                 |
| Materialized state store (RocksDB)                | `processors/content-ledger/src/main/java/com/edpo/contentledger/stream/ContentLedgerTopology.java` (`Stores.persistentKeyValueStore`, `Materialized.as`)                                                           |
| Interactive Queries                               | `processors/content-ledger/src/main/java/com/edpo/contentledger/web/LedgerQueryService.java`, `processors/content-ledger/src/main/java/com/edpo/contentledger/web/LedgerController.java`                           |
| Event-time orientation                            | `processors/content-ledger/src/main/java/com/edpo/contentledger/stream/EventNormalizer.java` (`eventTime`), `processors/content-ledger/src/main/java/com/edpo/contentledger/stream/PayloadEventTimeExtractor.java` |
| Out-of-order and duplicate handling               | `processors/content-ledger/src/main/java/com/edpo/contentledger/model/ContentDecisionState.java` (`insertSorted`, `seenEventIds`)                                                                                  |


## Run

From project root:

```bash
docker compose -f docker-compose.infra.yml up -d
docker compose up -d --build content-ledger
```

For realistic event traffic, also run the upstream services:

```bash
docker compose up -d --build user-service verification-service reporting-service attestation-service notification-service
```

Endpoints:

- `http://localhost:8085` (content-ledger API)
- `http://localhost:8086` (UI proxy, if `ui` service is running)

## API

```bash
curl "http://localhost:8085/api/content?limit=20&withState=true"
curl "http://localhost:8085/api/content/<contentId>/state"
curl "http://localhost:8085/api/content/<contentId>/decision-trace"
curl "http://localhost:8085/api/health/stream"
```

## Practical Demo

Run the included end-to-end script:

```bash
./processors/content-ledger/scripts/demo.sh
```

The script drives user registration, verification, and reporting via the platform services and auto-completes required Camunda user tasks so events end up in the ledger.

## Important Correlation Rule

The ledger correlates by `contentId`.

- `verification-service` derives `contentId` from `contentUrl`
- `reporting-service` uses `postId` as correlation key

To merge both flows into one ledger entry, use the verification-derived `contentId` as `postId` when creating a report. The demo script handles this automatically.

## Troubleshooting

- If `GET /api/content/<id>/state` returns `not_found`:
  - Check stream health: `curl http://localhost:8085/api/health/stream`
  - Check upstream services are running
  - Check events are produced to the input topics

## References

- Root setup and platform flow: `[README.md](../../README.md)`
- Demo script for end-to-end ledger flow: `[processors/content-ledger/scripts/demo.sh](./scripts/demo.sh)`
- Postman collection: `[misc/content-ledger-postman-collection.json](../../misc/content-ledger-postman-collection.json)`
- Related SPA (SLA monitor): `[processors/sla-monitor/README.md](../sla-monitor/README.md)`
- SLA flow context used by both SPAs: `[docs/processing/sla-monitor-flow.md](../../docs/processing/sla-monitor-flow.md)`

