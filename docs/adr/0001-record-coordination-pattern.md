# 0001 - Coordination Pattern: Hybrid Orchestration and Choreography

14.04.2026

## Status

Accepted

## Context

Our platform needs to coordinate work across five services: `user-service`, `verification-service`, `reporting-service`, `attestation-service`, and `notification-service`. These match three distinct business flows: user registration, content verification, and content reporting. Each of these flows has a different character. The content verification flow, for example, involves waiting for an unpredictable number of peer verdicts, enforcing configurable timeouts, making a binary approve-or-reject decision, and potentially issuing a cryptographic signature. These are multi-step, stateful, legally significant outcomes where we need to know at any moment exactly where a given request stands.

The notification flow, on the other hand, is a side-effect. When a verification completes or a post gets deleted, any number of downstream consumers need to be informed, but none of them need to coordinate with each other, and no consumer failing should block the primary flow from completing.

Two established coordination patterns exist for this kind of system. Pure orchestration places a central workflow engine in charge of every interaction — every step, every timeout, every conditional branch is explicitly modelled and driven by the engine. This gives excellent visibility and control but creates a deployment dependency on the engine for every inter-service interaction, including simple notifications. Pure choreography does the opposite: services react to events independently with no central coordinator, which is great for loose coupling but makes the stateful, conditional business flows hard to reason about, hard to observe, and very difficult to compensate correctly when something goes wrong.

The tension is real. Our platform has flows that genuinely need the tight control of orchestration and others where choreography is the more natural and maintainable fit.

### Decision Drivers

- Reliability
- Scalability
- Maintainability

### Options Considered

- **Option 1**: Pure Orchestration. Pros: Centralized control, easy to monitor. Cons: Tight coupling, single point of failure.
- **Option 2**: Pure Choreography. Pros: Loose coupling, highly scalable. Cons: Hard to monitor, complex error handling.
- **Option 3**: Hybrid Approach (Chosen). Pros: Best of both worlds. Cons: Increased complexity.

## Decision

We adopt a hybrid coordination strategy: Camunda 8 (Zeebe) orchestrates all three core business processes `RegisterUser.bpmn`, `VerifyContent.bpmn`, and `ReportContent.bpmn` while Apache Kafka handles the loosely coupled, side-effect notification flows.

The rule for which pattern to apply is straightforward. Any flow with complex conditional branching, asynchronous waits with defined timeouts, legally or reputationally sensitive outcomes, or required compensating actions belongs in a BPMN model under Zeebe's control. All three of our business processes satisfy these criteria. The content verification flow alone involves a user-registration check, dispatching requests to peers, waiting for their verdicts (with a timeout), running an internal check, calling the attestation service, and finally notifying the requester. Each step represented as an explicit BPMN task with its own retry and failure semantics.

Notifications such as `verification-notification`, `report-notification`, `user-registered`, `user-rejected`, `post-deleted`, and `objection-approved` are published to Kafka topics by the relevant service once the orchestrated step completes. The `notification-service` subscribes to all of these topics independently. This service has no HTTP interface at all; it is a pure Kafka consumer. If it restarts or falls behind, the primary workflows are completely unaffected, and no messages are lost because Kafka retains the log.

## Consequences

The hybrid approach means each coordination style is used where it actually fits, rather than being stretched to cover use cases it handles poorly. For the orchestrated flows, every in-flight process instance is durably persisted by Zeebe, observable in Camunda Operate, and recoverable after a worker crash without any application-level persistence code on our part. For the notification flow, new consumers can subscribe to existing Kafka topics at any time without changing anything in the producing services.

The downside is that the team needs to understand both patterns well. Debugging a process that spans both a BPMN engine and a Kafka topic requires familiarity with Zeebe's gRPC API, Camunda Operate, and Kafka consumer group offsets simultaneously. There is also a meaningful operational dependency: Zeebe must be running and reachable for any new process instance to start. The `notification-service`, by contrast, is entirely optional from the perspective of the core flows — a nice property that pure orchestration would not give us.

### Positive Consequences

The orchestrated core flows benefit from the centralised control that pure orchestration provides: every BPMN task has explicit retry semantics, every in-flight instance is durably persisted by Zeebe, and every step is fully auditable via Camunda Operate without any additional logging code. At the same time, the Kafka-based notification side retains the loose coupling and scalability that pure choreography offers: the `notification-service` is entirely independent of the services that produce events, can fall behind or restart without affecting core flows, and can be scaled horizontally via consumer group partitioning. New consumers (an analytics service, an email gateway) can subscribe to existing topics at any time without touching existing producers or redeploying any orchestrated service.

### Negative Consequences

The main drawback of this implementation is the increased complexity it comes with. The operational surface area is larger than a pure choreography system. The team must understand both Zeebe and Kafka, and failures require diagnosis in two different subsystems. So, there is also a drawback in the maintainability of the implementation.

### Implementation Considerations

Any new business flow added to the platform should be assessed against the same criteria we applied here. If it requires stateful waits, defined timeouts, conditional branching, or compensating transactions, it belongs in a BPMN model. If it is a side-effect that should not block the primary flow, Kafka is the right vehicle.

