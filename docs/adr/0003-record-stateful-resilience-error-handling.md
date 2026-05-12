# 0003 - Stateful Resilience and Error Handling

## Status

Accepted

## Context

Our platform runs long-lived, asynchronous verification and reporting processes. A `VerifyContent` instance might be suspended for minutes waiting for peer verdicts. A `ReportContent` instance might be waiting for a post owner to submit an objection within a defined window. During that time, any of the following can happen: a service container restarts due to a deployment, Zeebe's gRPC connection temporarily drops, the `user-service` is briefly unresponsive, or a developer's laptop goes to sleep.

The question is what happens to in-flight process instances and job executions when any of these events occur. If we have to design and maintain our own crash-recovery layer, writing process state to a database, checking for abandoned instances on startup, and re-enqueueing jobs, the services become significantly more complex and the risk of subtle bugs in the recovery logic is high.

### Options Considered

**Application-level checkpointing:** each service writes its own in-flight state to a database and reconciles on startup. Pros: no hard dependency on the engine for recovery. Cons: significant extra code per service, high risk of subtle bugs in reconciliation logic, retry counter management must be built and maintained from scratch.

**Zeebe's built-in job activation and retry model (chosen):** the engine marks jobs as active with a configurable timeout; jobs not acknowledged within that window are automatically re-queued. Pros: zero application-level recovery code, engine-guaranteed retry semantics, crash-safe by design. Cons: Zeebe availability becomes a hard dependency for all process progression.

**Human fallback via Camunda Tasklist for irreversible steps (chosen):** instead of automated retry for destructive operations, failed tasks are routed to a human moderator via a User Task. Pros: prevents accidental double-execution of irreversible actions, makes oversight explicit in the BPMN model and auditable in Operate. Cons: requires someone to actively monitor the Tasklist; introduces an operational SLA concern.

## Decision

We rely on Zeebe's built-in job activation and retry model as our primary resilience mechanism for transient failures. If the worker does not complete or fail the job because the service restarted, threw an unhandled exception, or lost its connection, Zeebe automatically makes the job available for the next poll cycle without any application-level intervention. Our services do not need to write their own retry or recovery logic for this class of failure; the engine handles it.

For retries on transient errors we configure the BPMN service tasks with an explicit retry count. When a worker explicitly fails a job (via a Zeebe job failure), Zeebe decrements the counter and re-activates the job after a back-off delay. 

We follow the at-least-once approach described in the course material: domain state is committed before the Zeebe job is completed. This accepts the possibility of duplicate processing after recovery, but avoids silently losing a state transition. In practice, this means our resilience strategy depends on persisted workflow state and eventual consistency rather than distributed transactions.
