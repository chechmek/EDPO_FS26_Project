# 0004 - Distributed Data Consistency: Saga Pattern over Distributed ACID Transactions

## Status

Accepted

## Context

Our platform spans five services, each with its own data store. The content verification flow touches at least three of them: `verification-service` validates the requester against `user-service`, issues a signature through `attestation-service`, and notifies downstream consumers via Kafka. 

In a traditional relational system, changes spanning multiple tables within a single database can be wrapped in an ACID transaction. If any step fails, the whole thing rolls back atomically. In our architecture, the same guarantee is fundamentally unavailable: the services run in separate processes with separate databases, and there is no transaction coordinator that can acquire locks across all of them simultaneously.

The standard solution for this in a distributed system is a Saga — a sequence of local transactions, each committed independently, where a failure at step N triggers compensating transactions to undo steps 1 through N-1. 

### Options Considered

**Shared database:** all services write to a common PostgreSQL schema. Pros: ACID transactions available natively, trivial cross-service queries. Cons: tight schema coupling forces coordinated deployments, a single database failure takes down all services, and independent scaling becomes impractical.

**Distributed transactions:** a transaction coordinator acquires locks across all service databases before committing. Pros: strong consistency across services. Cons: not practically implementable across independent Python microservices; introduces a coordinator as a new single point of failure; high latency under lock contention.

**Saga pattern with Zeebe orchestration (chosen):** each service commits its own local transaction; compensating actions for failure scenarios are modelled explicitly as BPMN branches. Pros: no distributed locks, services remain independently deployable and evolvable, compensation logic is visible in the process model rather than scattered across event handlers. Cons: eventual consistency windows exist between service-local state and the process variables in Zeebe; compensation is semantic rather than physical rollback.

## Decision

We keep a strict database-per-service model and coordinate cross-service consistency with Zeebe-based Saga orchestration. All three business flows (`RegisterUser`, `VerifyContent`, and `ReportContent`) follow the Saga approach: each service commits local state in its own boundary, and overall consistency is reached through workflow progression instead of a global ACID transaction.

At the current project stage, we do not implement explicit rollback-style compensating transactions for already committed steps. Our failure handling is based on forward recovery (retries, timeout paths, rejection/end states, and human intervention where needed).

If this system is extended toward production-grade guarantees, we can introduce explicit compensation steps for selected scenarios where business requirements demand semantic undo across services.
