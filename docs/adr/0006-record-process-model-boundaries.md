# 0006 - Process Model Boundaries: BPMN Models and Bounded Contexts

## Status

Accepted

## Context

Our system is split into five bounded contexts, each with a different responsibility:

- **User Identity Context** (`user-service`): handles user onboarding and registration status.
- **Content Verification Context** (`verification-service`): runs peer-based content verification and triggers attestation for approved content.
- **Reporting Context** (`reporting-service`): handles reports, objection windows, moderation decisions, and deletion flow.
- **Attestation Context** (`attestation-service`): stores and invalidates signatures via REST APIs.
- **Notification Context** (`notification-service`): consumes Kafka events and sends/logs notifications as side effects.

The architecture question is where process boundaries should be drawn. We can either combine these flows into one large BPMN process, or keep process ownership aligned with bounded contexts. Since these contexts evolve independently and have different runtime behavior, one shared process model would create unnecessary coupling and coordination overhead.

### Options Considered

**Single unified BPMN model (process monolith):** all flows modeled as one process definition or as sub-processes of one. Pros: single deployment artifact, shared process variables across flows. Cons: any change requires redeploying the whole model; teams across domains must coordinate releases around one shared artifact.

**One BPMN model per bounded context (chosen):** each domain service owns, deploys, and operates its own process definition. Pros: independent deployment cycles, clear ownership, easier scaling and troubleshooting per domain. Cons: cross-context linking must be handled through APIs/events and shared IDs rather than one global process state.

## Decision

We align process boundaries with bounded context boundaries.

`RegisterUser.bpmn` is owned by the User Identity context, `VerifyContent.bpmn` by the Content Verification context, and `ReportContent.bpmn` by the Reporting context. Each service runs only the Zeebe workers for its own task types and does not execute tasks from other domains.

The Attestation and Notification contexts are intentionally not modeled as standalone BPMN processes. Attestation remains a dedicated REST capability used by orchestrated flows, and Notification remains a Kafka-driven side-effect consumer. This keeps orchestration focused on core business processes while preserving loose coupling for infrastructure-style concerns.
