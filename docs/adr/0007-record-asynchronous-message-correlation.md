# 0007 - Asynchronous Message Correlation via Zeebe

## Status

Accepted

## Context

Two workflows in our system wait for external callbacks:

- `VerifyContent.bpmn` waits for a correlated peer decision message (`peer-approved` / `peer-rejected`), while `verification-service` collects individual peer verdict callbacks until that decision can be emitted.
- `ReportContent.bpmn` waits for post-owner objections.

The main problem is routing each callback to the correct waiting process instance at scale, without polling loops or manual process-instance lookups in application code.

It is also important to separate this from synchronous utility calls. `attestation-service` is used through direct REST calls inside service tasks (publish/invalidate signature). These are request/response steps in the flow, not asynchronous callback correlation points.

### Options Considered

**Synchronous blocking REST call:** the callback handler looks up the process instance key and calls Zeebe's REST API directly to complete the waiting receive task. Pros: simple, one call per callback. Cons: tightly couples the HTTP layer to engine internals; fragile under client retries without idempotency guards; the handler must manage job activation lifecycle itself.

**Application-level polling:** a periodic background task checks a database for pending verdicts and resumes the process accordingly. Pros: decouples the HTTP handler from the engine. Cons: polling delay between verdict submission and process resumption; adds a database dependency; correlation logic moves out of the process model and into application code.

**Zeebe native message correlation (chosen):** the callback handler publishes a named Zeebe message with a correlation key; Zeebe routes it internally to the matching suspended instance. Pros: no polling, no secondary routing tables, message is buffered by the engine if the instance has not yet reached its wait state, wait state is visible in Camunda Operate. Cons: messages with no matching subscriber are silently dropped after the configured TTL.

## Decision

We use Zeebe native message correlation for asynchronous callbacks and keep the waiting logic in BPMN.

In `ReportContent`, once a report is marked valid, the process enters an event-based wait: either an objection message arrives or the deadline timer fires. The objection endpoint (`POST /reports/{reportId}/objection`) publishes `post-owner-objection` with `reportId` as correlation key. In auto-object mode, the same message is published by the simulation path after a delay. This means both manual and automated objections use one correlation mechanism and resume the exact waiting instance.

In `VerifyContent`, `verification-service` collects individual peer verdict callbacks and publishes the correlated process decision message (`peer-approved` or `peer-rejected`) using `verificationId` as correlation key.

`attestation-service` remains outside this pattern by design: it is a synchronous REST utility used inside service tasks for signature create/invalidate operations, not an asynchronous callback participant.
