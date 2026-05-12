# Proposal P2 – Content Decision Ledger

## Goal

Maintain a continuously updated, queryable audit ledger of all decisions made about each
content item over its lifecycle. Any content submitted to the platform passes through multiple
decisions: verification (approved or rejected), reporting (report accepted or dismissed),
deletion, and objection review. This proposal builds a materialized view (KTable) that captures
the current state and full decision history per content item.

This is the core **Auditability** capability of the stream processing layer: given a `contentId`,
retrieve the full decision trace as a low-latency query without scanning any database.

---

## Input Streams

| Topic | Role |
|---|---|
| `verification-notification` | Verification decisions (verified / rejected) |
| `report-notification` | Moderation events (report accepted/valid, reporterId) |
| `post-deleted` | Content deletion event |
| `objection-approved` | Objection approved (deletion overruled) |

---

## Output

| Output | Type | Description |
|---|---|---|
| `content-decision-ledger` | Kafka topic (log-compacted) | Changelog of content state (CQRS read-side) |
| `contentStateTable` | KTable state store | Latest decision state per contentId |
| REST `/content/{contentId}/state` | Interactive Query endpoint | Current content lifecycle state |
| REST `/content/{contentId}/decision-trace` | Interactive Query endpoint | Full timeline of decisions |

---

## Stream Processing Topology

> **Rendering note**: Mermaid diagrams render natively on GitHub and in VS Code's built-in Markdown Preview (View → Open Preview). No plugin required for VS Code ≥ 1.77.

```mermaid
flowchart TD
    VN[(verification-notification)]
    RN[(report-notification)]
    PD[(post-deleted)]
    OA[(objection-approved)]

    VN --> MV1[mapValues\nnormalize → ContentEvent]
    RN --> MV2[mapValues\nnormalize → ContentEvent]
    PD --> MV3[mapValues\nnormalize → ContentEvent]
    OA --> MV4[mapValues\nnormalize → ContentEvent]

    MV1 --> SK[selectKey · contentId]
    MV2 --> SK
    MV3 --> SK
    MV4 --> SK

    SK --> MRG[merge\nall 4 streams]
    MRG --> GBK[groupByKey · contentId]
    GBK --> AGG[aggregate\n→ ContentDecisionState]

    AGG --> CST[(contentStateTable\nKTable · Materialized)]
    AGG --> TS[toStream]
    TS --> OUT[(content-decision-ledger\nlog-compacted)]

    CST -.->|Interactive Query| IQ1[[REST /content/{id}/state]]
    CST -.->|Interactive Query| IQ2[[REST /content/{id}/decision-trace]]
```

**Key topology decisions:**
- `mapValues` on each stream is a **stateless** operation (Week 8 – Single-Event Processing).
- `selectKey(contentId)` triggers **repartitioning** so all events for the same content land
  on the same partition (Week 8 – Multiphase Processing / Repartitioning).
- `merge` joins four streams into one without requiring time-aligned events (not a join but a
  union — semantically different from a windowed join).
- `groupByKey + aggregate` builds the **KTable** materialised view (Week 9 – KStream/KTable
  duality, State Stores).
- **Interactive Queries** on `contentStateTable` enable low-latency read access (Week 9).
- The log-compacted output topic is a **reprocessing-friendly** event source (Week 8 –
  Reprocessing pattern): if logic changes, re-running from the compacted changelog rebuilds
  the state.

---

## Lecture Concepts Covered

| Concept | Week | How it is used |
|---|---|---|
| Single-Event Processing (map/filter) | 8 | Normalize each event type to a common schema |
| Repartitioning / selectKey | 8 | Co-locate all decisions per contentId |
| Reprocessing | 8 | Log-compacted output enables deterministic re-runs |
| KStream/KTable duality | 9 | Aggregate stream into current-state table |
| State Stores (RocksDB) | 9 | `contentStateTable` backed by changelog topic |
| Interactive Queries | 9 | REST endpoints on state store |
| Streams AND Tables | 9 | Streams as input, KTable as output |

---

## -ilities Supported

- **Auditability**: Complete decision history per content item, queryable in real time.
- **Fairness / Procedural Correctness**: Every moderation decision is recorded and traceable.
- **Resilience**: State store backed by changelog; rebuilt after restart without data loss.
- **Observability**: Operators can query content state without accessing Camunda/Zeebe internals.
- **Availability**: Read-side query API is independent of the core BPMN flows.

---

## Bundling Recommendations

- **P5 – Peer Reviewer Scorecard**: The Scorecard can join with `contentStateTable` to determine
  whether a peer's approval was later overruled (fairness accountability).
- **P6 – Audit Explainability Index**: P2 provides the content dimension; P6 adds the user
  dimension via a table-table join.
- **P1 – SLA Monitor**: SLA violations become more explainable when enriched with decision traces
  from P2.

A strong minimal bundle covering all assignment requirements: **P1 + P2 + P6**.

---

## Implementation Hint

New Java service `content-ledger-service` with:
- Kafka Streams topology as described.
- REST layer exposing two interactive query endpoints.
- The `content-decision-ledger` output topic should be configured as **log-compacted** in
  Kafka (retain latest value per key — matches KTable semantics).
- Avro schemas in `schemas/content-ledger/`: `ContentEvent.avsc`, `ContentDecisionState.avsc`.

Existing services need: `contentId` added to all relevant event payloads (or derive from
`contentUrl` hash inside the stream processor as a fallback).
