# Submission - Assignment 2

- Course: Event-driven and Process-oriented Architectures (EDPO), FS2026, University of St.Gallen
- Group 4
  - Evan Martino
  - Marco Birchler
  - Roman Babukh

## Repository and Presentation

- Project repository: [https://github.com/chechmek/EDPO_FS26_Project](https://github.com/chechmek/EDPO_FS26_Project)
- Assignment 2 implementation snapshot: TODO
- Assignment 2 presentation slides: TODO

## 1. General Description of the Project

Our software project is a peer-based content verification platform. A user first registers through a moderated onboarding process, then submits content for verification, after which peers review the submission and the platform performs additional internal checks. If the outcome is positive, the system creates a cryptographic attestation. Reported content can later enter a moderation process with human review and an objection window.

The first assignment established the platform as a hybrid EDPO architecture with bounded contexts, BPMN orchestration in Camunda 8, and asynchronous choreography via Kafka. Assignment 2 extends this architecture with a dedicated stream-processing layer that consumes the existing Kafka event bus without changing the upstream BPMN processes or service APIs. This allowed us to add read-side views, operational monitoring, and audit-oriented projections while preserving the decoupled event-driven style of the original system.

The stream-processing layer consists of two independent processors:

1. `processors/sla-monitor`: a Python processor that computes windowed verification metrics and tracks moderation SLA breaches.
2. `processors/content-ledger`: a Java / Kafka Streams processor that materializes the lifecycle of each content item into a queryable decision ledger.

Both processors consume the same four domain topics:

- `verification-notification`
- `report-notification`
- `post-deleted`
- `objection-approved`

They then derive specialized read models and monitoring outputs for different purposes.

### 1.1 Processing-Layer Overview

```mermaid
flowchart LR
    subgraph Core["Existing Core Platform"]
        US["user-service"]
        VS["verification-service"]
        RS["reporting-service"]
        ATT["attestation-service"]
        CAM["Camunda 8 / Zeebe"]
    end

    subgraph Kafka["Kafka Domain Topics"]
        VN[(verification-notification)]
        RN[(report-notification)]
        PD[(post-deleted)]
        OA[(objection-approved)]
    end

    subgraph P1["Processor I - SLA Monitor"]
        SM["Python processor"]
        M1[(verification-metrics-1m)]
        M2[(verification-metrics-5m-hop)]
        SV[(sla-violations)]
        IQ1["Flask IQ API"]
    end

    subgraph P2["Processor II - Content Decision Ledger"]
        CL["Kafka Streams processor"]
        LEDGER[(content-decision-ledger)]
        STORE[("content-state-store\nRocksDB")]
        IQ2["Spring IQ API"]
    end

    VS --> VN
    RS --> RN
    RS --> PD
    RS --> OA

    VN --> SM
    RN --> SM
    PD --> SM
    OA --> SM

    VN --> CL
    RN --> CL
    PD --> CL
    OA --> CL

    SM --> M1
    SM --> M2
    SM --> SV
    SM --> IQ1

    CL --> STORE
    CL --> LEDGER
    CL --> IQ2
```

## 2. Explicit References to Lecture and Exercise Concepts

Assignment 2 covers the task-sheet suggestions directly and also links back to broader EDPO concepts from Assignment 1.

### 2.1 Assignment-2 Stream-Processing Concepts

| Concept from class / task sheet | Where it appears in our project |
|---|---|
| Several stateless operations (Week 8) | `normalize`, `filter`, `branch`, `mapValues`, `selectKey` in both processors |
| Streams and tables together (Week 9) | `content-ledger` aggregates multiple `KStream`s into a materialized `KTable` |
| Data from more than one stream (Week 9) | both processors consume four input topics |
| Interactive Queries (Week 9) | Flask endpoints in `sla-monitor`, Spring REST endpoints in `content-ledger` |
| Windowed operations (Week 10) | 1-minute tumbling and 5-minute/1-minute hopping windows in `sla-monitor` |
| Event-time processing (Week 10) | both processors use `eventTime` from the payload rather than consumer wall-clock time |
| Out-of-order handling (Week 10) | late moderation outcomes buffered in `sla-monitor`; decision traces sorted by event time in `content-ledger` |

### 2.2 Broader EDPO Concepts Covered Across the Whole Project

| EDPO concept | How it is realized |
|---|---|
| Bounded contexts / domain decomposition | `user-service`, `verification-service`, `reporting-service`, `attestation-service`, `notification-service` |
| Process orchestration | Camunda 8 BPMN processes `RegisterUser`, `VerifyContent`, `ReportContent` |
| Event choreography | Kafka notification and outcome topics exchanged between services and processors |
| Human intervention | moderator tasks in registration and moderation flows |
| Stateful resilience | retry and wait states in BPMN from Assignment 1; materialized state in stream processors in Assignment 2 |
| CQRS-style read side | both processors build read models that are separate from the write-side services |
| Event sourcing / replay-oriented thinking | log-compacted ledger topic and deterministic event-time processing |
| Eventual consistency | processors derive state asynchronously from Kafka rather than updating a shared database synchronously |

## 3. Stream Processing App 1 - SLA Monitor

### 3.1 Goal

The SLA Monitor provides real-time observability over the verification process and moderation pipeline. It answers two practical questions:

1. What verification outcomes are currently occurring, and at what rate?
2. Are accepted moderation cases resolved within the target SLA window?

This processor therefore supports both operational monitoring and policy enforcement.

### 3.2 Topology Graph

```mermaid
flowchart TD
    VN[(verification-notification)]
    RN[(report-notification)]
    PD[(post-deleted)]
    OA[(objection-approved)]

    C[consume + parse JSON]
    N[normalize + filter]
    B{branch by event kind}

    TW1[1-minute tumbling window]
    TW2[5-minute / 1-minute hopping window]
    AGG1[aggregate verification counts]
    AGG2[aggregate trend metrics]

    SLA[SLA state tracker]
    OPEN[("_open_sla_cases")]
    RECENT[("_recent_sla_outcomes")]
    VIOL[("_sla_violations")]

    OUT1[(verification-metrics-1m)]
    OUT2[(verification-metrics-5m-hop)]
    OUT3[(sla-violations)]

    IQ[[GET /metrics/verification\nGET /sla/open-cases\nGET /sla/violations]]

    VN --> C
    RN --> C
    PD --> C
    OA --> C
    C --> N
    N --> B

    B -->|verification| TW1
    B -->|verification| TW2
    TW1 --> AGG1 --> OUT1
    TW2 --> AGG2 --> OUT2

    B -->|report accepted / deletion / objection approved| SLA
    SLA --> OPEN
    SLA --> RECENT
    SLA --> VIOL
    VIOL --> OUT3

    AGG1 --> IQ
    AGG2 --> IQ
    VIOL --> IQ
    OPEN --> IQ
```

### 3.3 Detailed Description and Justification

The processor is intentionally split into two logical sub-topologies.

The first sub-topology handles verification metrics. Incoming `verification-notification` events are normalized, filtered, and counted by outcome. We use a 1-minute tumbling window for short-lived operational snapshots and a 5-minute hopping window with 1-minute advance for smoother trend analysis. This combination mirrors the lecture discussion of windowing trade-offs: tumbling windows give clean per-interval summaries, while hopping windows provide overlap that better captures trends.

The second sub-topology handles moderation SLA tracking. A `report-notification` event with `status=report-accepted` opens an SLA case keyed by `contentId`. A `post-deleted` or `objection-approved` event closes the case. If no closing outcome arrives before the deadline, the processor emits a `sla-violations` event. Because the Python implementation uses `confluent-kafka` rather than the Kafka Streams DSL, the correlation is implemented as a manual stateful join with in-memory structures instead of a declarative KStream-KStream join. This was a deliberate trade-off: it let us build the processor quickly in a language already used by the rest of the platform, at the cost of manually implementing state handling, late-event buffering, and breach scanning logic.

### 3.4 Lecture Concepts Realized

| Concept | How it appears |
|---|---|
| Single-Event Processing | normalize and filter each message before routing |
| Branch pattern | split verification events from moderation outcome events |
| Re-keying / repartitioning conceptually | correlation is done by `contentId` |
| Stream-stream join conceptually | accepted reports are matched with later outcomes |
| Local materialized state | `_open_sla_cases`, `_recent_sla_outcomes`, `_sla_violations` |
| Interactive Queries | Flask endpoints expose current in-memory state |
| Tumbling window | 1-minute verification aggregates |
| Hopping window | 5-minute windows advanced every minute |
| Event time | `eventTime` is used for window placement and SLA age |
| Grace / late events | outcomes can arrive late or before the start event |

---

## 4. Stream Processing App 2 - Content Decision Ledger

### 4.1 Goal

The Content Decision Ledger builds a queryable per-content audit trail. Every event that affects a content item is folded into a single `ContentDecisionState` so that the current lifecycle state and the chronological decision trace can be retrieved with low latency.

This processor is the read-side projection that most directly supports explainability and auditability.

### 4.2 Topology Graph

```mermaid
flowchart TD
    VN[(verification-notification)]
    RN[(report-notification)]
    PD[(post-deleted)]
    OA[(objection-approved)]

    N1[mapValues -> ContentEvent]
    N2[mapValues -> ContentEvent]
    N3[mapValues -> ContentEvent]
    N4[mapValues -> ContentEvent]

    K1[selectKey(contentId)]
    K2[selectKey(contentId)]
    K3[selectKey(contentId)]
    K4[selectKey(contentId)]

    M[merge 4 streams]
    G[groupByKey]
    A[aggregate -> ContentDecisionState]

    STORE[("content-state-store\nRocksDB + changelog")]
    LEDGER[(content-decision-ledger)]
    IQ[[GET /api/content\nGET /api/content/{id}/state\nGET /api/content/{id}/decision-trace]]

    VN --> N1 --> K1 --> M
    RN --> N2 --> K2 --> M
    PD --> N3 --> K3 --> M
    OA --> N4 --> K4 --> M

    M --> G --> A
    A --> STORE
    A --> LEDGER
    STORE --> IQ
```

### 4.3 Detailed Description and Justification

The topology first normalizes four heterogeneous input streams into a unified `ContentEvent` model. This is a Week-8 style stateless mapping step that reduces downstream complexity. The processor then re-keys every event by `contentId`, merges the streams, groups by key, and aggregates them into a `ContentDecisionState`.

The aggregation result is materialized as a `KTable` backed by RocksDB and a Kafka changelog. This directly uses the KStream/KTable duality covered in class: the incoming event streams are the append-only fact log, while the table is the latest derived state per content item. The same state is also emitted to the log-compacted topic `content-decision-ledger`, which makes the read model replayable and durable.

The aggregator is deliberately idempotent. Every event carries or derives an `eventId`, and duplicates are skipped via `seenEventIds`. We also sort the decision trace by `eventTime` on insertion so that reprocessing and late arrivals remain deterministic. This matters because audit-style read models lose credibility quickly if duplicates or out-of-order artifacts appear in the visible history.

### 4.4 Lecture Concepts Realized

| Concept | How it appears |
|---|---|
| Stateless `mapValues` | each topic is normalized independently |
| `selectKey` / repartitioning | all events for one `contentId` are co-located |
| Stream merge | four streams become one logical event stream |
| Streams and tables together | KStream inputs aggregated into a KTable |
| `groupByKey + aggregate` | builds the materialized lifecycle projection |
| State store | `content-state-store` in RocksDB |
| Interactive Queries | Spring REST layer reads the local Kafka Streams store |
| Reprocessing pattern | compacted output topic can rebuild state |
| Event-time hygiene | `PayloadEventTimeExtractor` reads payload timestamps |
| Idempotency and dedupe | repeated `eventId`s are ignored |

---

## 5. Architectural Decisions and Trade-offs

### 5.1 Python Consumer vs Kafka Streams DSL

We intentionally implemented the two processors differently.

- `sla-monitor` in Python minimized setup cost and reused our existing stack, but required manual state handling, manual late-event logic, and offered no built-in changelog-backed recovery.
- `content-ledger` in Kafka Streams DSL required a heavier Java/Spring toolchain, but provided a much more declarative topology, built-in materialized state, RocksDB persistence, and direct support for Interactive Queries.

This contrast was useful for learning because it made the value of Kafka Streams stateful abstractions concrete rather than theoretical.

### 5.2 JSON at Runtime vs Avro on the Wire

The task sheet suggests Avro, and we partially implemented this direction by providing explicit Avro schemas for the ledger under `schemas/content-ledger/`. At runtime, however, we kept JSON because the upstream Python services already publish JSON and we wanted to avoid the additional operational complexity of introducing Schema Registry late in the semester.

The trade-off is clear:

- JSON reduced integration friction and let us focus on stream-processing concepts.
- Avro would provide stronger schema evolution guarantees and stricter contracts.

Our practical conclusion is that JSON was the right short-term choice for the course prototype, while Avro remains the more scalable production direction.

### 5.3 Event Time vs Processing Time

Both processors rely on `eventTime` contained in the payload, not consumer wall-clock time. This decision was essential for replayability, late-event handling, and correct window semantics. The downside is that late and out-of-order events must be handled explicitly, which increased design complexity in both processors.

### 5.4 Event-Driven Read Models vs Shared Database Reads

We deliberately built read-side projections from Kafka rather than querying the services' internal state stores directly. This preserves bounded-context autonomy and avoids coupling the processors to service implementation details. The cost is eventual consistency: the queryable read models update asynchronously and may temporarily lag behind the command side.

---

## 6. Additional Results and Insights

### 6.1 What We Learned from Kafka Streams

The Content Decision Ledger made the main Kafka Streams concepts tangible:

- a `KTable` is not just a theory artifact; it is an effective way to expose current state from an event log,
- local RocksDB state plus a changelog gives a strong operational model for recovery,
- log compaction is a natural fit when the output semantics are "latest state per key".

### 6.2 What We Learned from Windowing and Late Events

The SLA Monitor showed that windowing is easy to describe conceptually but more subtle in implementation. The important issue was not only choosing tumbling versus hopping windows, but also deciding what to do with events that arrive late or in reverse order. This reinforced the lecture point that event-time semantics and grace handling are first-class design decisions, not implementation details.

### 6.3 What We Learned from Combining the Two Processors

Using the same four topics for two separate processors demonstrated a core event-driven benefit: one event log can support multiple independent downstream views. One processor serves observability and alerting, while the other serves auditability and low-latency query access. No upstream BPMN process had to change.

---

## 7. Release Reference and Implementation Links

- Assignment 2 snapshot: [https://github.com/chechmek/EDPO_FS26_Project/tree/1b91ee9](https://github.com/chechmek/EDPO_FS26_Project/tree/1b91ee9)
- Root project documentation: `[README.md](../README.md)`
- SLA monitor flow description: `[docs/processing/sla-monitor-flow.md](../processing/sla-monitor-flow.md)`
- Content ledger documentation: `[processors/content-ledger/README.md](../../processors/content-ledger/README.md)`
- Proposal P1: `[docs/proposals/01-verification-activity-monitor.md](../proposals/01-verification-activity-monitor.md)`
- Proposal P2: `[docs/proposals/02-content-decision-ledger.md](../proposals/02-content-decision-ledger.md)`
- ADRs from Assignment 1: `[docs/adr/](../adr/)`

Relevant implementation folders:

- SLA monitor: `[processors/sla-monitor/](../../processors/sla-monitor/)`
- Content ledger: `[processors/content-ledger/](../../processors/content-ledger/)`
- Shared schemas: `[schemas/content-ledger/](../../schemas/content-ledger/)`
- Load/demo scripts: `[scripts/](../../scripts/)`

---

## 8. Assignment 1 Companion Artifacts

The Exercise 8 hand-in also requires the final Assignment 1 material. The relevant companion artifacts in this repository are:

- Final Assignment 1 report: `[submission-assignment-1.md](./submission-assignment-1.md)`
- Assignment 1 PDF: `[submission-assignment-1-complete.pdf](./submission-assignment-1-complete.pdf)`
- Assignment 1 slide deck: `[submission-assignment-1-slides.pdf](./submission-assignment-1-slides.pdf)`
- Assignment 1 repository tag: [https://github.com/chechmek/EDPO_FS26_Project/tree/assignment-1](https://github.com/chechmek/EDPO_FS26_Project/tree/assignment-1)

Compared to the earlier mid-semester state, the final project version adds the full stream-processing layer described in this report, integrates the standalone UI, hardens the content-ledger startup behavior, and extends the supporting scripts and documentation. Responsibilities remain split as described in the contribution section above and in `[submission-responsibilities.md](./submission-responsibilities.md)`.

## 9. Reflections and Lessons Learned

The main lesson from Assignment 2 is that stream processing is most valuable when it extends an already event-driven system instead of duplicating the command-side logic. In our case, the BPMN-driven platform already emitted the right domain events; Kafka Streams and a lightweight Python processor let us turn those events into useful read models with minimal disruption.

We also learned that stateful processing quickly forces architectural discipline. Keys must be chosen carefully, timestamps must be trustworthy, duplicate handling must be explicit, and late arrivals require a policy. These aspects were easy to underestimate at the design level but became central during implementation.

Finally, the assignment helped us connect the semester topics more clearly: BPMN orchestration manages long-running business processes, Kafka provides decoupled domain event exchange, and stream processing turns those event logs into live operational and analytical views. Together, these patterns form a coherent EDPO architecture rather than isolated course topics.

---

## 10. References

- Assignment 2 task sheet: `[EDPO_FS26_E8.pdf](../exercises-tasksheets/EDPO_FS26_E8.pdf)`
- Assignment 2 presentation: `[EDPO-Final-Presentation-2.pdf](./EDPO-Final-Presentation-2.pdf)`
- Assignment 1 submission: `[submission-assignment-1.md](./submission-assignment-1.md)`
- Exercise 4 submission: `[submission-exercise-4.md](./submission-exercise-4.md)`
- Exercise 5 submission: `[submission-exercise-5.md](./submission-exercise-5.md)`
