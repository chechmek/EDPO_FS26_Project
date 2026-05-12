# 0002 - Workflow Engine Selection: Camunda 8 over Camunda 7

## Status

Accepted

## Context

We need a BPMN workflow engine to orchestrate our three core business processes. The two candidates in the Camunda ecosystem are Camunda 7 (the long-established, Spring Boot native platform) and Camunda 8 (the newer, Zeebe-based engine built around an event-sourced, distributed architecture).

Our team writes Python 3.12. None of our services use Java or Spring Boot, and we have no intention of introducing either. 

Camunda 7 was designed around a Java process engine. Its REST API can technically be called from any language, but the available Python clients are thin wrappers that require managing raw HTTP, polling, and serialization by hand. There is no officially supported, idiomatically Python job-worker client for Camunda 7. Running job workers against it from Python means either a bespoke polling loop or a third-party library with no active maintenance.

Camunda 8 has `pyzeebe`, an actively maintained, asyncio-native Python library that provides first-class job-worker support, message publication, and process lifecycle management over gRPC. This is not a workaround - it is the intended integration path for Python services.

The second relevant dimension is state persistence. Camunda 7 stores process state in a relational database (typically PostgreSQL or MySQL). All variable mutations, token movements, and wait states require transactional writes to a shared schema. Under high concurrency this becomes a bottleneck and a single point of failure. Camunda 8's engine (Zeebe) uses an event-sourced log persisted to RocksDB, partitioned across brokers. Process state is the accumulated result of an append-only log; the engine never needs to update rows, only append records.

### Options Considered

**Camunda 7** has a mature ecosystem and strong tooling, but its process engine is Java-native. Its REST API can be called from Python, but there is no officially supported, idiomatic Python job-worker client. Running workers from Python requires either a hand-rolled polling loop or an unmaintained third-party library. Process state is stored in a relational database, which becomes a write bottleneck under high concurrency. Rejected on grounds of interoperability and scalability.

**Camunda 8 / Zeebe (chosen)** provides `pyzeebe`, an actively maintained asyncio-native Python library with first-class job-worker support over gRPC. Its event-sourced, append-only persistence to RocksDB removes the relational write bottleneck. Process state survives worker crashes by design, with no application-level persistence code required.

## Decision

We use Camunda 8 (Zeebe), operated in self-managed mode via `c8run`, as the workflow engine for all three BPMN processes. Our Python services interact with it exclusively through `pyzeebe`, which is used in all three orchestrated services: `user-service`, `verification-service`, and `reporting-service`. Each service runs a `ZeebeWorker` in a dedicated asyncio event loop on a background thread, and uses `ZeebeClient` when it needs to start a process instance or publish a message.

We run Zeebe in single-broker mode locally, which is sufficient for course-project scale. The architecture does not require changes to move to a multi-broker cluster because the `ZEEBE_ADDRESS` environment variable is the only coupling point between the services and the engine.

