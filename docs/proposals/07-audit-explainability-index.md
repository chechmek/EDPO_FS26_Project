# Proposal P7 – Audit Explainability Index

## Goal

Build a cross-domain, queryable audit index that joins user state, content state, and moderation
outcomes into a unified explainability view. Given a `contentId`, an operator can ask: "Why was
this content deleted? Who submitted it? Was the submitter a registered user? What was the peer
verdict? Was there an objection?" — and get a single coherent answer from one REST endpoint.

This proposal demonstrates the **Table-Table Join** and **GlobalKTable** patterns, which are
the most complex join patterns in the lecture. It also serves as the "CQRS read side" for the
stream processing layer — a materialized, queryable audit index independent of Camunda Operate.

---

## Input Streams / Tables

| Source | Type | Role |
|---|---|---|
| `user-registered` / `user-rejected` | KStream → KTable | Latest user status per userId |
| `verification-notification` | KStream → KTable | Latest verification outcome per contentId |
| `report-notification` / `post-deleted` / `objection-approved` | KStream → KTable | Latest moderation outcome per contentId |

---

## Output

| Output | Type | Description |
|---|---|---|
| `audit-index-updates` | Kafka topic (log-compacted) | Changelog of joined audit records |
| `userOutcomeGlobalTable` | GlobalKTable | Registration status; fully replicated |
| `contentOutcomeTable` | KTable | Latest content lifecycle state |
| `auditIndexTable` | KTable | Joined user + content + moderation state |
| REST `/audit/content/{contentId}` | Interactive Query endpoint | Full explainability record |
| REST `/audit/user/{userId}` | Interactive Query endpoint | All content decisions for a user |
| REST `/audit/explain?contentId=...` | Interactive Query endpoint | Human-readable decision trace |

---

## Stream Processing Topology

> **Rendering note**: Mermaid diagrams render natively on GitHub and in VS Code's built-in Markdown Preview (View → Open Preview). No plugin required for VS Code ≥ 1.77.

```mermaid
flowchart TD
    subgraph GT["Step 1 · User Status GlobalKTable"]
        UR[(user-registered)] --> SK1[selectKey · userId]
        URJ[(user-rejected)] --> SK2[selectKey · userId]
        SK1 & SK2 --> MRG1[merge]
        MRG1 --> MV1[mapValues · UserOutcome]
        MV1 --> UGKT[("userOutcomeGlobalTable\nGlobalKTable\nfully replicated on all instances")]
    end

    subgraph CT["Step 2 · Content Outcome KTable"]
        VN[(verification-notification)] --> SK3[selectKey · contentId]
        RN[(report-notification)] --> SK4[selectKey · contentId]
        PD[(post-deleted)] --> SK5[selectKey · contentId]
        OA[(objection-approved)] --> SK6[selectKey · contentId]
        SK3 & SK4 & SK5 & SK6 --> MRG2[merge]
        MRG2 --> MV2[mapValues · normalize → ContentOutcome]
        MV2 --> GBK[groupByKey · contentId]
        GBK --> AGG1[aggregate\n→ ContentOutcomeState]
        AGG1 --> COT[(contentOutcomeTable · KTable)]
    end

    subgraph JOIN["Step 3 · Table–Table Join → Audit Index"]
        COT --> TS1[toStream]
        TS1 --> J1[join\nKStream–GlobalKTable\nby userId]
        UGKT --> J1
        J1 --> GBK2[groupByKey · contentId]
        GBK2 --> AGG2[aggregate\n→ AuditIndexRecord]
        AGG2 --> AIT[(auditIndexTable · KTable)]
        AGG2 --> TS2[toStream]
        TS2 --> OUT[(audit-index-updates\nlog-compacted)]
    end

    AIT -.->|Interactive Query| IQ1[[REST /audit/content/{id}]]
    AIT -.->|Interactive Query| IQ2[[REST /audit/user/{id}]]
    AIT -.->|Interactive Query| IQ3[[REST /audit/explain]]
```

**Key topology decisions:**
- **GlobalKTable** for user status solves the **External Lookup Problem** (Week 8/9): every
  stream processor instance has the full user table locally, so a join can happen for any
  partition without network calls.
- **KStream-GlobalKTable join** is the enrichment join pattern (Week 8/9 – Stream-Table Join).
- **Table-Table join** (conceptually): `contentOutcomeTable` ⨝ `userOutcomeGlobalTable`
  materializes a combined audit record. In Kafka Streams this is implemented as a
  KStream-GlobalKTable join after streaming the table (Week 9 – Table-Table Join).
- `selectKey`, `mapValues`, `filter` are **stateless** throughout (Week 8).
- The log-compacted output topic supports **Reprocessing** if join logic changes (Week 8).
- **Interactive Queries** on `auditIndexTable` are the primary output (Week 9).

---

## Lecture Concepts Covered

| Concept | Week | How it is used |
|---|---|---|
| Single-Event Processing (map/filter) | 8 | Normalize each event type to common schema |
| Repartitioning / selectKey | 8 | Co-locate by contentId / userId |
| External Lookup Problem | 8 | Solved via GlobalKTable for user status |
| Stream-Table Join | 8/9 | Enrich content stream with user status |
| Table-Table Join | 9 | Join content outcome table with user status table |
| KTable vs GlobalKTable | 9 | GlobalKTable chosen for cross-partition lookup |
| State Stores | 9 | RocksDB-backed auditIndexTable |
| Streams AND Tables | 9 | Multiple KTables and GlobalKTable in one topology |
| Interactive Queries | 9 | REST endpoints reading directly from state stores |
| Reprocessing | 8 | Log-compacted output enables re-runs |

---

## -ilities Supported

- **Auditability**: Single-endpoint, comprehensive audit record for any content item.
- **Fairness / Procedural Correctness**: Explainability — every decision can be traced back to
  its cause and actor.
- **Observability**: Operators can investigate any platform decision without accessing Zeebe
  internals.
- **Resilience**: State stores backed by Kafka changelog; `GlobalKTable` rebuilt from source
  topic on restart.
- **Availability**: Read-side query API independent of core BPMN flows.

---

## Bundling Recommendations

This proposal is best understood as a **cross-cutting read-side** that integrates data from the
other stream processing applications:
- **P2 – Content Decision Ledger**: P7 is a superset of P2 — if both are implemented, P7 can
  consume from `content-decision-ledger` (P2's output) instead of raw topics.
- **P3 – User Reputation Store**: P7 and P3 share the user dimension; `userReputationTable`
  (P3) can replace or complement `userOutcomeGlobalTable` in P7's join.
- **P4 – Moderation Consistency Checker**: P7 audit records provide context when P4 raises
  an inconsistency alert.
- **P5 – Peer Reviewer Scorecard**: Peer verdict details from P5 can be included in the
  explainability record.

**Recommended complete bundle covering all requirements: P1 + P2 + P7**
- P1 covers stateless ops + windowed aggregations + event time.
- P2 covers KTable, IQ, multi-stream merge.
- P7 covers GlobalKTable, Table-Table Join, advanced IQ.

---

## Implementation Hint

New Java service `audit-index-service`:
- Kafka Streams topology as described.
- REST endpoints for interactive queries (3 endpoints as listed above).
- `userId` (submitter) must be included in `verification-notification` payload so the join to
  user state is possible.
- `contentId` must be consistent across `verification-notification`, `report-notification`,
  `post-deleted`, and `objection-approved`.
- Avro schemas: `UserOutcome.avsc`, `ContentOutcomeState.avsc`, `AuditIndexRecord.avsc`.
- The `/audit/user/{userId}` endpoint may require a full state store scan if no secondary index
  is maintained — acceptable at course scale; document as architectural trade-off.
