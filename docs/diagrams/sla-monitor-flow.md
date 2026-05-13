# SLA Monitor Flow

This diagram reflects the repository implementation of `processors/sla-monitor` and the
Kafka-only load-generation workaround used for stream testing.

```mermaid
flowchart TD
    subgraph Prod["Production Event Sources"]
        VS["verification-service"]
        RS["reporting-service"]
        VS --> T1[(verification-notification)]
        RS --> T2[(report-notification)]
        RS --> T3[(post-deleted)]
        RS --> T4[(objection-approved)]
    end

    subgraph Monitor["processors/sla-monitor"]
        C["Kafka consumer"]
        N["normalize + filter"]
        B{"branch by event kind"}
        M1["1-minute tumbling metrics"]
        M2["5-minute hopping metrics"]
        S["SLA case tracker\nopen / resolved / breach"]
        API["Flask query API"]

        C --> N
        N --> B
        B -->|verification-notification| M1
        B -->|verification-notification| M2
        B -->|report-notification\npost-deleted\nobjection-approved| S
        M1 --> O1[(verification-metrics-1m)]
        M2 --> O2[(verification-metrics-5m-hop)]
        S --> O3[(sla-violations)]
        M1 --> API
        M2 --> API
        S --> API
    end

    T1 --> C
    T2 --> C
    T3 --> C
    T4 --> C

    API --> R1[[GET /metrics/verification]]
    API --> R2[[GET /sla/open-cases]]
    API --> R3[[GET /sla/violations]]
```

## Interpretation

- Production path: domain services publish notification/outcome events as part of the normal
  BPMN-driven flows.
- Test path: `scripts/load_sla_monitor.py` bypasses Camunda user-task bottlenecks and writes
  directly to the same Kafka topics.
- Processor path: `sla-monitor` consumes the shared event log, computes windowed verification
  metrics, tracks report SLA state, emits breach alerts, and exposes query endpoints over its
  in-memory materialised state.
