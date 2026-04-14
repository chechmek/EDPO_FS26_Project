# 0002 - Workflow Engine Selection: Camunda 8 over Camunda 7

14.04.2026

## Status

Accepted

## Context

We need a BPMN workflow engine to orchestrate our three core business processes. The two most natural candidates in the Camunda ecosystem are Camunda 7 (the long-established, Spring Boot native platform) and Camunda 8 (the newer, Zeebe-based engine built around an event-sourced, distributed architecture).

Our team writes Python 3.12. None of our services use Java or Spring Boot, and we have no intention of introducing either. This is not a stylistic preference — it is a project constraint, because mixing runtimes would fragment our build pipeline, container definitions, and team knowledge. The client library situation for the two engines is therefore the most important practical factor in the decision.

Camunda 7 was designed around a Java process engine. Its REST API can technically be called from any language, but the available Python clients are thin wrappers that require managing raw HTTP, polling, and serialization by hand. There is no officially supported, idiomatically Python job-worker client for Camunda 7. Running job workers against it from Python means either a bespoke polling loop or a third-party library with no active maintenance.

Camunda 8 has `pyzeebe`, an actively maintained, asyncio-native Python library that provides first-class job-worker support, message publication, and process lifecycle management over gRPC. This is not a workaround — it is the intended integration path for Python services.

The second relevant dimension is state persistence. Camunda 7 stores process state in a relational database (typically PostgreSQL or MySQL). All variable mutations, token movements, and wait states require transactional writes to a shared schema. Under high concurrency this becomes a bottleneck and a single point of failure. Camunda 8's engine (Zeebe) uses an event-sourced log persisted to RocksDB, partitioned across brokers. Process state is the accumulated result of an append-only log; the engine never needs to update rows, only append records.

### Decision Drivers

- Interoperability: Python 3.12 is the sole runtime; no Java or Spring Boot dependency may be introduced
- Reliability: process state must survive worker crashes and redeployments without application-level checkpointing
- Scalability: the persistence model must not become a bottleneck under concurrent process instances
- Maintainability: adding new job workers must require minimal boilerplate

### Options Considered

**Camunda 7** has a mature ecosystem and strong tooling, but its process engine is Java-native. Its REST API can be called from Python, but there is no officially supported, idiomatic Python job-worker client. Running workers from Python requires either a hand-rolled polling loop or an unmaintained third-party library. Process state is stored in a relational database, which becomes a write bottleneck under high concurrency. Rejected on grounds of interoperability and scalability.

**Temporal / Conductor** both have Python SDKs, but neither executes BPMN natively. Our requirement is to model and run processes as BPMN on a BPMN engine. Rejected on grounds of BPMN incompatibility.

**Camunda 8 / Zeebe (chosen)** provides `pyzeebe`, an actively maintained asyncio-native Python library with first-class job-worker support over gRPC. Its event-sourced, append-only persistence to RocksDB removes the relational write bottleneck. Process state survives worker crashes by design, with no application-level persistence code required.

## Decision

We use Camunda 8 (Zeebe), operated in self-managed mode via `c8run`, as the workflow engine for all three BPMN processes. Our Python services interact with it exclusively through `pyzeebe`, which is used in all three orchestrated services: `user-service`, `verification-service`, and `reporting-service`. Each service runs a `ZeebeWorker` in a dedicated asyncio event loop on a background thread, and uses `ZeebeClient` when it needs to start a process instance or publish a message.

We run Zeebe in single-broker mode locally, which is sufficient for course-project scale. The architecture does not require changes to move to a multi-broker cluster because the `ZEEBE_ADDRESS` environment variable is the only coupling point between the services and the engine.

We considered self-hosted Camunda 7 briefly. The Python integration story was the deciding factor against it. We also looked at lightweight alternatives such as Temporal and Conductor. Both have Python SDKs, but neither supports BPMN natively — our course requirement is to model processes in BPMN and execute them on a BPMN engine, so that ruled them out.

## Consequences

### Positive Consequences

`pyzeebe` gives our Python services a clean, idiomatic way to implement job workers using simple `async def` functions decorated with `@worker.task(task_type="...")`. Writing a new job worker takes a few lines of code and no knowledge of gRPC internals. Message correlation and process launching follow the same pattern. The event-sourced persistence model means that process variable changes survive worker restarts without any application-level persistence logic. If a service crashes between a Zeebe job activation and its completion, Zeebe retries the job automatically once the retry timeout elapses. We do not need to checkpoint our own state to a database for crash recovery. Horizontal scaling is a configuration change rather than an architectural one.

### Negative Consequences

Zeebe requires Java 21 to 23 to run in `c8run` mode, which is a runtime dependency outside our Python ecosystem. Every developer needs a working Java installation to run the full stack locally. Zeebe also consumes more memory than a lightweight message router, and its RocksDB data directory requires some care to manage, particularly around disk space during long-running development sessions. Camunda 7's richer query API and direct SQL access to the process history database are not available to us; for audit and monitoring purposes we rely entirely on Camunda Operate, which is adequate but less flexible than raw SQL queries would be.