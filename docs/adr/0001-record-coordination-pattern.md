# 0001 - Coordination Pattern: Hybrid Orchestration and Choreography

## Status

Accepted

## Context

Our platform needs to coordinate work across five services: `user-service`, `verification-service`, `reporting-service`, `attestation-service`, and `notification-service`. These involve three distinct business flows: user registration, content verification, and content reporting. These are multi-step, stateful flows where we need to know at any moment exactly where a given request stands.

The notification flow, on the other hand, is a side-effect. When a verification completes or a post gets deleted, any number of downstream consumers need to be informed, but none of them need to coordinate with each other, and no consumer failing should block the primary flow from completing.

### Options Considered

- **Option 1**: Pure Orchestration. Pros: Centralized control, easy to monitor. Cons: Tight coupling, single point of failure.
- **Option 2**: Pure Choreography. Pros: Loose coupling, highly scalable. Cons: Hard to monitor, complex error handling.
- **Option 3**: Hybrid Approach (Chosen). Pros: Best of both worlds. Cons: Increased complexity.

## Decision

We adopt a hybrid coordination strategy: Camunda 8 (Zeebe) orchestrates all three core business processes `RegisterUser.bpmn`, `VerifyContent.bpmn`, and `ReportContent.bpmn` while Apache Kafka handles the loosely coupled, side-effect notification flows.

The rule for which pattern to apply is straightforward. Any flow with complex conditional branching or asynchronous waits with defined timeouts belongs in a BPMN model under Zeebe's control. All three of our business processes satisfy these criteria. The content verification flow alone involves a user-registration check, dispatching requests to peers, waiting for their verdicts (with a timeout), running an internal check, calling the attestation service, and finally notifying the requester. Each step represented as an explicit BPMN task with its own retry and failure semantics.

Notifications such as `verification-notification`, `report-notification`, `user-registered`, `user-rejected`, `post-deleted`, and `objection-approved` are published to Kafka topics by the relevant service once the orchestrated step completes. The `notification-service` subscribes to all of these topics independently. This service has no HTTP interface at all; it is a pure Kafka consumer. If it restarts or falls behind, the primary workflows are completely unaffected, and no messages are lost because Kafka retains the log.

