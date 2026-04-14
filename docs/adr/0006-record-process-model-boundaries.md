# 0006 - Process Model Boundaries: One BPMN Model per Bounded Context

14.04.2026

## Status

Accepted

## Context

When Camunda is first introduced to a system, there is a tempting but dangerous pattern: create one large BPMN process model that handles everything. Start with user registration, then extend the same model to cover content verification, then add reporting as a sub-process. The result is what practitioners call a process monolith — a single workflow definition that becomes a coordination point for the entire application.

The problem with a process monolith is the same as with any monolith. A change to the peer voting logic in the verification flow requires redeploying the same model that also governs user onboarding. A bug in the report objection timer blocks a hotfix to the registration background check. Teams that own different domains are forced to coordinate releases around a shared artifact. Worse, a high volume of content verification instances inflates the Zeebe partition load for all processes, including the low-volume, low-urgency user registrations.

Our platform has three domains that are conceptually independent: user identity (registration and onboarding), content trust (verification, peer voting, signature issuance), and content governance (reporting, objection, moderation, deletion). They share some data — `userId` appears in both verification and registration — but their lifecycles are completely separate. A user registration can complete without any content verification ever being started. A report can be filed against a post whose original verification process completed months ago.

### Decision Drivers

- Maintainability: a change to one process model must not require redeployment of unrelated services
- Scalability: load from one domain must not inflate partition or resource usage for another
- Elasticity: each service and its workers must be scalable independently of the others

### Options Considered

**Single unified BPMN model (process monolith):** all three flows modelled as one process definition or as sub-processes of one. Pros: single deployment artifact, shared process variables across flows. Cons: any change requires redeploying the whole model; high-volume verification instances inflate Zeebe partition load for all flows including low-volume registrations; teams across domains must coordinate every release around one shared artifact.

**One BPMN model per bounded context (chosen):** each service owns, deploys, and operates its own process definition. Pros: fully independent deployment cycles, domain-isolated partition load, teams can evolve their model without any cross-domain coordination. Cons: cross-domain data correlation (e.g. linking a report back to its original verification) must be handled by convention in application code rather than shared process state.

## Decision

We define three independent BPMN process models, each owned and deployed by its corresponding service:

1. `RegisterUser.bpmn` (`Process_1kwkl0j`) is owned and driven by `user-service`. It handles background-checked onboarding and publishes `user-registered` or `user-rejected` events to Kafka when it concludes.

2. `VerifyContent.bpmn` (`Process_01gn4xr`) is owned and driven by `verification-service`. It handles the full peer verification lifecycle, including user eligibility checks, peer distribution, verdict collection, internal review, attestation, and outcome notification.

3. `ReportContent.bpmn` (`Process_0rsygf3`) is owned and driven by `reporting-service`. It covers moderation review, owner notification and objection window, and the conditional deletion path with signature invalidation.

Each service registers its own `ZeebeWorker` that handles only the task types declared in its own BPMN model. There is no cross-service worker — `verification-service` does not handle any task declared in `ReportContent.bpmn`, and vice versa. The process IDs are stable constants in each service (`VERIFY_CONTENT_PROCESS_ID`, `REPORT_CONTENT_PROCESS_ID`) and are never shared across service boundaries.

The `attestation-service` is intentionally excluded from this model. It is not orchestrated by Camunda at all — it provides a REST API for signature storage and invalidation that the orchestrated services call as part of their own task handlers. It is an infrastructure service, not a domain process.

## Consequences

### Positive Consequences

Services and their process models are independently deployable and scalable. Deploying a change to `VerifyContent.bpmn` is an operation performed only by the team responsible for `verification-service`; it requires no coordination with the user or reporting teams. Load from one domain does not affect the operational performance of another. Zeebe handles concurrent instances of different process definitions independently, so scaling `verification-service` horizontally to handle a spike in verification requests does not affect the instance throughput of `RegisterUser` processes at all. The independence also simplifies monitoring. In Camunda Operate, filtering by process definition gives a clean view of just the instances relevant to one domain. Diagnosing a stuck verification does not require sifting through registration or report instances.

### Negative Consequences

Certain cross-cutting concerns must be handled by convention rather than shared infrastructure. The `userId` that appears in both `RegisterUser.bpmn` and `VerifyContent.bpmn` is not enforced by a shared domain object; `verification-service` validates it with a REST call to `user-service` at the start of the process. Similarly, the correlation between a `reportId` in `ReportContent.bpmn` and the original verification that produced the post's signature is maintained by the `reporting-service` locally, not by a shared process link. Cross-domain queries such as "show me the full lifecycle of a piece of content from submission to deletion" require aggregating data from multiple services and process definitions, because there is no built-in join in Zeebe or Camunda Operate across process definitions.

