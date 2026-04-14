# 0007 - Asynchronous Message Correlation via Zeebe

14.04.2026

## Status

Accepted

## Context

Two of our three BPMN flows require the process engine to wait for external signals that arrive at unpredictable times. In `VerifyContent.bpmn`, a process instance is suspended at a message catch event after peer requests are dispatched. It waits there until a peer submits a verdict via `POST /verifications/{verificationId}/peer-response`. The process does not know in advance how long that will take — it might be seconds in an automated test or hours in a real scenario. In `ReportContent.bpmn`, a process instance waits for the post owner to optionally file an objection via `POST /reports/{reportId}/objection` within a defined deadline.

The core challenge is routing: when a peer verdict arrives at the `verification-service`, which specific process instance does it belong to? There could be hundreds of concurrent verifications in flight. Similarly, an objection arriving at the `reporting-service` must find its specific report process, not any other one.

The naive alternative is to implement this with synchronous blocking calls. The REST handler that receives the peer verdict could look up the process instance key and call the Zeebe REST API synchronously to complete the waiting receive task. This would work for simple cases but creates a tight coupling between the HTTP layer and the engine, requires the handler to manage the activation lifecycle itself, and would struggle with exactly-once semantics if the REST call is retried.

Another alternative is to have the process poll for verdicts on a timer. The service could implement a periodic task that checks a database for pending verdicts and resumes the process accordingly. This moves the correlation logic out of the engine and into application code, increasing complexity and creating a polling delay between verdict submission and process resumption.

### Decision Drivers

- Performance: peer verdict and objection callbacks must reach their waiting process instance with low latency, without polling delays
- Reliability: a message published before the target instance reaches its wait state must not be silently lost
- Scalability: thousands of concurrently suspended instances must be supportable at minimal active resource cost

### Options Considered

**Synchronous blocking REST call:** the callback handler looks up the process instance key and calls Zeebe's REST API directly to complete the waiting receive task. Pros: simple, one call per callback. Cons: tightly couples the HTTP layer to engine internals; fragile under client retries without idempotency guards; the handler must manage job activation lifecycle itself.

**Application-level polling:** a periodic background task checks a database for pending verdicts and resumes the process accordingly. Pros: decouples the HTTP handler from the engine. Cons: polling delay between verdict submission and process resumption; adds a database dependency; correlation logic moves out of the process model and into application code.

**Zeebe native message correlation (chosen):** the callback handler publishes a named Zeebe message with a correlation key; Zeebe routes it internally to the matching suspended instance. Pros: no polling, no secondary routing tables, message is buffered by the engine if the instance has not yet reached its wait state, wait state is visible in Camunda Operate. Cons: messages with no matching subscriber are silently dropped after the configured TTL.

## Decision

We use Zeebe's native message correlation mechanism for all asynchronous callbacks. When an external signal arrives — a peer verdict or an owner objection — the receiving REST endpoint publishes a named Zeebe message with a correlation key that uniquely identifies the target process instance. Zeebe routes the message to the correct suspended instance and delivers the associated variables into the process scope.

For peer verdicts in `verification-service`, the correlation key is the `verificationId` (a UUID generated at process start and passed as an initial process variable). The message name is either `peer-approved` or `peer-rejected`, depending on the accumulated vote tally. The `_publish_camunda_message` function calls `ZeebeClient.publish_message(name, correlation_key, variables)` using the shared asyncio event loop.

For owner objections in `reporting-service`, the correlation key is the `reportId`. The message name is `post-owner-objection`. The objection REST handler calls `ZeebeClient.publish_message` with the explanation payload and the `reportId` as the correlation key.

In both cases, the BPMN process models have corresponding message catch events subscribed to these message names, with the correlation key expression referencing the appropriate process variable (`= verificationId` or `= reportId`). Zeebe enforces that a message is delivered to exactly one instance — if no instance is waiting for a given correlation key, the message is buffered for a configurable TTL and then discarded.

## Consequences

### Positive Consequences

Process instances that are waiting for external signals do not poll. They are fully suspended in Zeebe, consuming no CPU, no memory in the application tier, and no database connections until the correlated message arrives. This is important for scalability: thousands of verifications can be simultaneously suspended with zero active resource consumption. The `verificationId` and `reportId` are stable, client-visible identifiers that peers and post owners use in their callback requests. Because they are also the Zeebe correlation keys, there is no secondary mapping table required to connect an incoming REST request to its process instance. The routing is done entirely inside the engine. The BPMN models accurately represent the wait semantics as message catch events, which are visible in Camunda Operate. An engineer looking at a stuck verification instance can immediately see that it is suspended at "Waiting for Peer Verdict" and that the expected message has not arrived.

### Negative Consequences

Correlation keys must be unique per active message subscription. If two concurrent processes subscribe to the same message name with the same correlation key, the behaviour is undefined. Our UUIDs make this collision extremely unlikely, but it is still a design constraint for any future correlated flow. Messages published without a matching subscriber are also silently dropped after the TTL. If a peer submits a verdict after the verification process has already timed out and completed, the message finds no subscriber and is discarded. This is the correct behaviour, but callers receive no error indicating the verdict was ignored. If needed, we would have to implement a response check at the application layer.

