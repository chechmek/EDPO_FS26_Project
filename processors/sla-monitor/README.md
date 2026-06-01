# SLA Monitor

Small stream processor for moderation SLA tracking and verification metrics.

## What It Does

- Consumes Kafka topics: `verification-notification`, `report-notification`, `post-deleted`, `objection-approved`
- Exposes read APIs on port `8005`
- Publishes derived topics:
  - `verification-metrics-1m`
  - `verification-metrics-5m-hop`
  - `sla-violations`

This processor is implemented in plain Python (`confluent-kafka` + Flask), but it follows the same stream-processing pattern as the Java Kafka Streams app. The consumer loop (`_consumer_loop`) reads the four input topics, parses JSON, and normalizes records (`_normalize_*`) into a unified internal shape, which is the Python equivalent of the Java-side stateless `mapValues` normalization. Routing in `_process_event` mirrors a Kafka Streams branch: verification events go to windowed metric aggregation, while moderation events go to SLA correlation.

Window logic is implemented explicitly in dictionaries (`_verification_metrics_1m`, `_verification_metrics_5m`) via `_append_verification_metric`, which corresponds to windowed aggregation in Kafka Streams (tumbling + hopping windows). SLA tracking uses keyed in-memory state (`_open_sla_cases`, `_recent_sla_outcomes`, `_sla_violations`) to emulate a state store and a stream-stream join by `contentId`: `report-accepted` opens a case, `post-deleted`/`objection-approved` closes it, and `_check_sla_breaches` emits violations when no closing event arrives in time. Finally, Flask endpoints expose the current materialized state as interactive queries, conceptually equivalent to querying a Kafka Streams state store through REST.

## Lecture Concepts Realized


| Concept                                                   | Where to find it in code                                                                                                       |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Stateless single-event processing (`normalize`, `filter`) | `processors/sla-monitor/app.py` (`_normalize_verification_event`, `_normalize_report_event`, `_normalize_outcome_event`)       |
| Branch pattern (verification vs moderation/SLA path)      | `processors/sla-monitor/app.py` (`_process_event`)                                                                             |
| Tumbling window (1 minute)                                | `processors/sla-monitor/app.py` (`_append_verification_metric`, `_build_window`)                                               |
| Hopping window (5 minute window, 1 minute hop)            | `processors/sla-monitor/app.py` (`_append_verification_metric`, `_build_window`)                                               |
| Event-time processing                                     | `processors/sla-monitor/app.py` (`_parse_event_time`, `_append_verification_metric`, `_check_sla_breaches`)                    |
| Out-of-order handling                                     | `processors/sla-monitor/app.py` (`_recent_sla_outcomes`, `_open_sla_case`, `_close_sla_case`)                                  |
| Interactive queries                                       | `processors/sla-monitor/app.py` (`/metrics/verification`, `/sla/open-cases`, `/sla/violations`)                                |
| Stateful materialized view (in-memory)                    | `processors/sla-monitor/app.py` (`_verification_metrics_1m`, `_verification_metrics_5m`, `_open_sla_cases`, `_sla_violations`) |


## Run

From project root:

```bash
docker compose -f docker-compose.infra.yml up -d
docker compose up -d --build sla-monitor
```

Health check:

```bash
curl http://localhost:8005/health
```

## Query APIs

```bash
curl http://localhost:8005/metrics/verification
curl "http://localhost:8005/metrics/verification?window=1m&limit=10"
curl "http://localhost:8005/metrics/verification?window=5m&limit=10"

curl http://localhost:8005/sla/open-cases
curl http://localhost:8005/sla/violations
curl "http://localhost:8005/sla/violations?include_resolved=true"
```

## Useful Load Scripts

From project root:

```bash
python scripts/load_sla_monitor.py --verification-events 500 --report-cases 200
python scripts/preview_sla_monitor.py
```

- `load_sla_monitor.py`: Kafka-only synthetic load for the SLA processor
- `preview_sla_monitor.py`: small end-to-end preview with live verification traffic and synthetic moderation events

## Environment Variables

- `KAFKA_BOOTSTRAP_SERVERS` (default: `kafka:29092`)
- `KAFKA_GROUP_ID` (default: `sla-monitor`)
- `PORT` (default: `8005`)
- `SLA_SECONDS` (default: `259200`, 72h)
- `WINDOW_GRACE_SECONDS` (default: `10`)
- `WINDOW_RETENTION_MINUTES` (default: `180`)
- `SCAN_INTERVAL_SECONDS` (default: `1`)

## References

- Root setup and platform flow: `[README.md](../../README.md)`
- SLA flow and correlation logic: `[docs/processing/sla-monitor-flow.md](../../docs/processing/sla-monitor-flow.md)`
- Synthetic load generator: `[scripts/load_sla_monitor.py](../../scripts/load_sla_monitor.py)`
- End-to-end preview script: `[scripts/preview_sla_monitor.py](../../scripts/preview_sla_monitor.py)`
- Curated stream stories (ledger + SLA): `[scripts/publish_content_stories.py](../../scripts/publish_content_stories.py)`
- Stream demo reset script: `[scripts/clean_stream_demo.sh](../../scripts/clean_stream_demo.sh)`
- Related SPA (content ledger): `[processors/content-ledger/README.md](../content-ledger/README.md)`

