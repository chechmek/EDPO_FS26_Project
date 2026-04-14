# 0004 - Distributed Data Consistency: Saga Pattern over Distributed ACID Transactions

14.04.2026

## Status

Accepted

## Context

Our platform spans five services, each with its own data store. The content verification flow touches at least three of them: `verification-service` validates the requester against `user-service`, issues a signature through `attestation-service`, and notifies downstream consumers via Kafka. The reporting flow adds a fourth party — `attestation-service` again — when it invalidates a signature on post deletion.

In a traditional relational system, changes spanning multiple tables within a single database can be wrapped in an ACID transaction. If any step fails, the whole thing rolls back atomically. In our architecture, the same guarantee is fundamentally unavailable: the services run in separate processes with separate databases, and there is no transaction coordinator that can acquire locks across all of them simultaneously.

The standard solution for this in a distributed system is a Saga — a sequence of local transactions, each committed independently, where a failure at step N triggers compensating transactions to undo steps 1 through N-1. Sagas come in two flavours: choreography-based (each service listens to events and decides what to do next) and orchestration-based (a central coordinator drives the sequence and triggers compensations explicitly).

A shared database approach — where all services write to a common PostgreSQL schema — was considered early and rejected. It would eliminate the distributed consistency problem at the cost of eliminating service independence: a schema change in one domain would require redeployment of every service, and a single database failure would take down the entire platform.

### Decision Drivers

- Reliability: partial failures across service boundaries must not leave the system in an undefined state
- Maintainability: services must be independently deployable; shared schemas create tight deployment coupling
- Fault Tolerance: a single database failure must not cascade across the entire platform

### Options Considered

**Shared database:** all services write to a common PostgreSQL schema. Pros: ACID transactions available natively, trivial cross-service queries. Cons: tight schema coupling forces coordinated deployments, a single database failure takes down all services, and independent scaling becomes impractical.

**Distributed transactions:** a transaction coordinator acquires locks across all service databases before committing. Pros: strong consistency across services. Cons: not practically implementable across independent Python microservices; introduces a coordinator as a new single point of failure; high latency under lock contention.

**Saga pattern with Zeebe orchestration (chosen):** each service commits its own local transaction; compensating actions for failure scenarios are modelled explicitly as BPMN branches. Pros: no distributed locks, services remain independently deployable and evolvable, compensation logic is visible in the process model rather than scattered across event handlers. Cons: eventual consistency windows exist between service-local state and the process variables in Zeebe; compensation is semantic rather than physical rollback.

## Decision

We enforce a strict database-per-service model and use Zeebe as the orchestration coordinator for our sagas. Each service owns its own data and does not write to any other service's store. Cross-service interactions happen exclusively through Zeebe job completions (for orchestrated steps) and Kafka events (for notifications).

Our saga variants follow Camunda's own classification. The `VerifyContent` flow resembles an **Epic Saga**: it is a long-running orchestration with a strict sequence of steps and a defined set of compensating paths if early steps succeed but later ones fail. If the `publish-signature` call to `attestation-service` fails after peer approval, the `send-verification-notification` task still runs — it just reports the appropriate failure status to the user rather than leaving the process in an undefined state.

The `ReportContent` flow is similar in structure but includes an explicit compensating step on the happy path. When a post is deleted, `delete-post` calls the attestation service to invalidate the associated signature. If that call fails, the post is still marked deleted locally and the outcome is logged — the business rule is that the deletion takes precedence, and reconciliation of the signature store is a background concern.

We do not implement a rollback for user registration because it is a one-way onboarding flow; a rejected user is simply never activated, which is a natural terminal state rather than a compensation.

## Consequences

### Positive Consequences

Services are deployable and scalable independently. Schema evolution in one service does not require coordinated changes across the system. The Zeebe-orchestrated saga gives us a significant advantage over a choreography-based saga for compensations: the process model explicitly models every branch, including failure paths, so compensating logic is visible in the BPMN diagram and not scattered across event handlers in multiple services. Any developer can open `VerifyContent.bpmn` in Camunda Modeler and see exactly what happens in each failure scenario.

### Negative Consequences

There are no cross-service foreign key constraints and no joins across service boundaries. Queries that would be trivial in a shared schema — "give me all verifications for a user along with their registration status" — require multiple API calls and application-level aggregation. Temporary inconsistencies between service-local state and the Zeebe process variables are possible after a service restart. For example, `attestation-service` may store a new signature before `verification-service` updates its in-memory record to reflect `"status": "verified"`. There is no distributed rollback — compensation is semantic, not physical — so careful design of compensating tasks is required for any new flow added to the system.