# 0003 - Stateful Resilience and Error Handling

14.04.2026

## Status

Accepted

## Context

Our platform runs long-lived, asynchronous verification and reporting processes. A `VerifyContent` instance might be suspended for minutes waiting for peer verdicts. A `ReportContent` instance might be waiting for a post owner to submit an objection within a defined window. During that time, any of the following can happen: a service container restarts due to a deployment, Zeebe's gRPC connection temporarily drops, the `user-service` is briefly unresponsive, or a developer's laptop goes to sleep.

The question is what happens to in-flight process instances and job executions when any of these events occur. If we have to design and maintain our own crash-recovery layer, writing process state to a database, checking for abandoned instances on startup, and re-enqueueing jobs, the services become significantly more complex and the risk of subtle bugs in the recovery logic is high.

There is also a second, more specific problem: the `delete-post` task in `ReportContent.bpmn` is irreversible. Once a post is deleted and its attestation signature is invalidated, that cannot be undone automatically. If something goes wrong during or immediately after this step, we need a human to review the situation rather than an automatic retry blindly executing the deletion a second time.

The retry problem is not limited to short-lived technical failures. In `VerifyContent.bpmn`, the critical failure mode is peer non-response across an asynchronous waiting period. This is exactly the kind of retry that cannot be handled safely inside a worker process, because the retry state must survive crashes, redeployments, and long timer waits. The retry counter therefore has to belong to the workflow state itself, not to application memory.

### Decision Drivers

- Reliability: process state and retry counters must survive worker crashes and redeployments
- Fault Tolerance: a single service failure must not abort an in-flight verification or report
- Resilience: transient failures must be retried automatically; irreversible actions require human oversight rather than blind automated retry

### Options Considered

**Application-level checkpointing:** each service writes its own in-flight state to a database and reconciles on startup. Pros: no hard dependency on the engine for recovery. Cons: significant extra code per service, high risk of subtle bugs in reconciliation logic, retry counter management must be built and maintained from scratch.

**Zeebe's built-in job activation and retry model (chosen):** the engine marks jobs as active with a configurable timeout; jobs not acknowledged within that window are automatically re-queued. Pros: zero application-level recovery code, engine-guaranteed retry semantics, crash-safe by design. Cons: Zeebe availability becomes a hard dependency for all process progression.

**Human fallback via Camunda Tasklist for irreversible steps (chosen):** instead of automated retry for destructive operations, failed tasks are routed to a human moderator via a User Task. Pros: prevents accidental double-execution of irreversible actions, makes oversight explicit in the BPMN model and auditable in Operate. Cons: requires someone to actively monitor the Tasklist; introduces an operational SLA concern.

## Decision

We rely on Zeebe's built-in job activation and retry model as our primary resilience mechanism for transient failures. When a `ZeebeWorker` activates a job, Zeebe marks that job as active and starts a configurable activation timeout. If the worker does not complete or fail the job within that window because the service restarted, threw an unhandled exception, or lost its connection, Zeebe automatically makes the job available for the next poll cycle without any application-level intervention. Our services do not need to write their own retry or recovery logic for this class of failure; the engine handles it.

For retries on transient errors we configure the BPMN service tasks with an explicit retry count. When a worker explicitly fails a job (via a Zeebe job failure response with `retries > 0`), Zeebe decrements the counter and re-activates the job after a back-off delay. In addition, `VerifyContent.bpmn` models a genuine stateful retry loop around peer non-response. After `Send Verification Request to Peers`, an Event-Based Gateway waits for either a peer response message or a timer. On timeout or service error, a Script Task increments the `send_verify_retry` process variable and an exclusive gateway routes either back to retry the peer request or forward to a failure notification once the retry cap is reached. The important point is that `send_verify_retry` is workflow state persisted by Zeebe, so the retry count survives crashes and redeployments even though the wait may last minutes.

We follow the at-least-once approach described in the course material: domain state is committed before the Zeebe job is completed. This accepts the possibility of duplicate processing after recovery, but avoids silently losing a state transition. In practice, this means our resilience strategy depends on persisted workflow state and eventual consistency rather than distributed transactions.

For the irreversible `delete-post` task we take a different approach. The BPMN model routes a failed deletion to a Camunda Tasklist User Task where a human moderator can inspect the state and decide whether to retry or dismiss the case. We also apply the same pattern to the `verify-user` and `check-report-valid` steps, which require human judgement by their nature. All three human tasks have corresponding Camunda Forms deployed alongside the BPMN (`verify-user.form`, `check-report-valid.form`, `review-objection.form`).

We deliberately do not implement application-level state checkpointing in any service. The process variables stored by Zeebe are the authoritative state for in-flight instances. The in-memory dictionaries in `verification-service` and `reporting-service` (`_verifications`, `_reports`) are a convenience layer for serving fast read queries; they are not the source of truth for process progression.

## Consequences

### Positive Consequences

Services contain no crash-recovery logic. There is no startup reconciliation code, no "check for stuck jobs on boot" logic, and no database schema for persisting retry counters. In-flight verifications and reports survive service restarts without data loss. Transient infrastructure hiccups such as a brief network partition between a service container and Zeebe, a pod restart during a rolling deployment, or a momentary timeout calling `user-service` are handled transparently. A process instance that was mid-execution when a container died will resume at the same task once the container comes back up and reconnects. The peer retry loop in `VerifyContent.bpmn` is genuinely stateful rather than an in-process retry, because the timeout state and the `send_verify_retry` counter are persisted in Zeebe and visible in Camunda Operate. Human fallback for irreversible decisions is built into the process model itself, making the oversight mechanism explicit and auditable.

### Negative Consequences

Zeebe is a hard dependency. Its availability directly governs the availability of our core flows. If Zeebe itself is unavailable, no new process instances can start and no in-flight jobs can progress. We accept this as a known single-point-of-failure for the current scale of the project. A production system would run Zeebe in a multi-broker, multi-partition cluster to remove this dependency. The in-memory service state (`_verifications`, `_reports`) is lost on a service restart, meaning GET endpoints return 404 for recently started instances until the process variables stored in Zeebe are eventually reconciled; this is an accepted limitation at the current project scale. The at-least-once completion strategy also means duplicate processing remains possible after recovery, so worker logic and downstream state transitions must remain idempotent. The use of Camunda Tasklist for human fallback also introduces an operational concern: someone needs to actively monitor the Tasklist and action stuck items. For the course project this is the team. For a real deployment, this would require a substantial amount of human resources and time to handle.