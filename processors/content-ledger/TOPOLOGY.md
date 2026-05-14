# Content Decision Ledger — Topology

The processing topology composes a handful of well-known stream-processing patterns into one Kafka Streams application:

- **Event Translator** (`mapValues`) — every input event is normalized into a uniform `ContentEvent`; status fields are canonicalized.
- **Repartitioning** (`selectKey(contentId)`) — co-locates all events for the same content item on the same partition.
- **Stream Merger** (`merge`) — joins the four input streams into one logical stream of decisions.
- **Local-State Aggregator** (`groupByKey().aggregate(...)`) — folds the merged stream into a `ContentDecisionState` per `contentId`. The aggregator is idempotent (`eventId` dedupe) and order-safe (trace sorted by `eventTime` on insert).
- **Materialized State** — the aggregate is held in `content-state-store` (RocksDB + changelog topic) and is the system of record.
- **Interactive Queries** — REST endpoints read the materialized state directly out of the local store.
- **Event Sink** — the same state is also emitted to the **log-compacted** topic `content-decision-ledger`, so any downstream consumer can re-derive the latest state by replay.

## Diagram

```text
    verification-      report-           post-            objection-
    notification       notification      deleted          approved
         │                  │                │                 │
         └──────────────────┴──────┬─────────┴─────────────────┘
                                   ▼
                      ┌────────────────────────────┐
                      │    Normalize + Re-key      │      Event Translator
                      │  (mapValues, selectKey)    │      + Repartitioning
                      └─────────────┬──────────────┘
                                    ▼
                      ┌────────────────────────────┐
                      │           Merge            │      Stream Merger
                      └─────────────┬──────────────┘
                                    ▼
                      ┌────────────────────────────┐
                      │         Aggregate          │      Local-State
                      │   (groupByKey, aggregate)  │      Aggregator
                      └─────────────┬──────────────┘
                                    │
               ┌────────────────────┴────────────────────┐
               ▼                                         ▼
      ╭──────────────────────╮               ┌─────────────────────────┐
      │   content-state-     │               │ content-decision-ledger │     Event Sink
      │   store (KTable)     │               │  (log-compacted topic)  │
      ╰──────────┬───────────╯               └─────────────────────────┘
                 ▲
                 │
     ┌───────────┴────────────┐
     │  Interactive Queries   │     Interactive Query Pattern
     │      (REST API)        │
     └────────────────────────┘
```
