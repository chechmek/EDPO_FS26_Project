# 0008 - Auditing and Read Models: Camunda Operate as a Separated CQRS Read Side

14.04.2026

## Status

Accepted

## Context

Our platform makes legally significant decisions: content is certified as trustworthy, posts are permanently deleted, users are approved or rejected based on background checks. For a system with these kinds of outcomes, auditability is not optional. Regulators, platform operators, and the users themselves have a legitimate interest in being able to ask: "What happened to this verification? Who approved it? When did the timeout fire? What was the state of the process when the deletion was triggered?"

The write side of our system — Zeebe — is an event-sourced engine. Every change to a process instance is recorded as an immutable event in an append-only log and snapshotted to RocksDB. This is excellent for durability and crash recovery, but it is a poor fit for rich, ad-hoc audit queries. Reading the history of a process instance from RocksDB requires either a Zeebe gRPC query or internal engine APIs. Neither is designed for efficient filtering, pagination, or cross-instance aggregation. Running audit queries against the execution engine also risks interfering with its performance under load.

The classic solution to this tension is CQRS (Command Query Responsibility Segregation): separate the model used to execute commands from the model used to answer queries. The execution engine is optimised for the write path; a separate read model is built from the same event log and optimised for queries.

Implementing our own CQRS read model would mean writing an event consumer that subscribes to the Zeebe event stream, transforming each event into a queryable representation, and maintaining that store. This is a significant engineering effort for a project that already has a broad scope.

### Decision Drivers

- Auditability: every step of every process must be traceable after the fact, including variable state, retries, and human task completions
- Performance: audit queries must not compete with execution engine throughput
- Maintainability: the audit infrastructure must require no bespoke development or ongoing schema maintenance

### Options Considered

**Custom CQRS read model:** write an event consumer that subscribes to the Zeebe export stream and projects events into a queryable store such as PostgreSQL. Pros: full control over the query schema, flexible reporting. Cons: significant engineering effort, a new service to deploy and maintain, schema must evolve in lockstep with BPMN model changes.

**Direct Zeebe gRPC queries for audit:** query process history via `ZeebeClient` API calls at query time. Pros: no additional infrastructure. Cons: Zeebe's gRPC API is not designed for ad-hoc filtering, pagination, or cross-instance aggregation; audit query load competes directly with execution engine throughput.

**Camunda Operate as read model (chosen):** Operate subscribes to the Zeebe event stream and maintains a continuously updated Elasticsearch-backed projection. Pros: zero custom code, rich filtering and pagination out of the box, query load is fully isolated from the execution engine, bundled with Camunda 8 Run at no extra setup cost. Cons: Elasticsearch adds meaningful memory overhead to the local stack; the read model is eventually consistent with a sub-second lag.

## Decision

We use Camunda Operate as the read model for all process audit and monitoring needs. Operate is bundled with Camunda 8 Run and runs as part of the same `c8run` deployment. Internally, Operate maintains a continuously updated projection of process instance state in Elasticsearch. It subscribes to the Zeebe event stream and indexes every process instance lifecycle event, variable change, job activation, message correlation, and error into Elasticsearch documents. This gives it fast, filterable, paginated access to the full history of every instance across all three process definitions.

The separation between Zeebe's execution engine and Operate's Elasticsearch-backed read model is precisely the CQRS split we need without having to build it ourselves. Queries to Operate's UI or REST API do not touch the Zeebe execution engine at all — they go directly to Elasticsearch. A spike in audit queries does not degrade process execution throughput.

For the specific accountability requirements of our platform, Operate provides everything we need: per-instance history with timestamps, variable state at each step, job retries and incident records, and message correlation events. An operator can open any process instance and see the exact sequence of events that led to its current state.

Camunda Tasklist serves a complementary role on the human task side. When a moderator action is required — reviewing a user background check via `verify-user.form`, validating a report via `check-report-valid.form`, or reviewing an owner objection via `review-objection.form` — the task and its associated data are visible and actionable in Tasklist. Completed human tasks are also recorded in Operate's audit history.

We do not build a custom event consumer or a separate audit database. The operational cost of maintaining a bespoke event projection is not justified given that Operate already provides the capability.

## Consequences

### Positive Consequences

Every step of a content verification, from the initial `check-user-registration` job through peer verdict correlation and attestation signature issuance, is traceable in Camunda Operate without any additional code. The same is true for the reporting and user registration flows. This satisfies our auditability requirement with zero application-level audit logging code. Full process instance history is available from the moment Camunda 8 Run starts. Audit queries are served from Elasticsearch, isolating query load from execution throughput. Human task completions are recorded as part of the same audit history. No custom event consumers or audit schemas need to be maintained.

### Negative Consequences

Operate's dependency on Elasticsearch means that a full local stack requires Elasticsearch to be running, which is part of `c8run` but adds memory overhead. In the default `c8run` configuration Elasticsearch listens on ports `9200` and `9300`, which can be a friction point on developer laptops with limited RAM. The Operate read model is eventually consistent with the Zeebe execution engine. There is a short lag, typically sub-second, between a process event occurring in Zeebe and that event appearing in Operate's Elasticsearch index. This is acceptable for audit and monitoring purposes, but the execution engine remains the authoritative source and Operate is only a projection of it. Custom audit reports that require joins across process definitions, or that need to correlate Zeebe history with data from application service databases, cannot be expressed in Operate's UI. For those cases, direct Elasticsearch queries against Operate's indices are possible but constitute an undocumented integration contract.

