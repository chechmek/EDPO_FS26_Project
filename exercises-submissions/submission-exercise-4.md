# Submission - Exercise 4

- Course: Event-driven and Process-oriented Architectures (EDPO), FS2026, University of St.Gallen
- Group 4
  - Evan Martino
  - Marco Birchler
  - Roman Babukh

## Repository

- GitHub repository: <https://github.com/chechmek/EDPO_FS26_Project>

## Team Contribution

- Evan Martino refined our core concepts and  BPMN processes, and made the documentation.  
- Marco Birchler created the orignal BPMN processes.

## 1. Orchestration vs. Choreography

All three processes (`VerifyContent`, `RegisterUser`, `ReportContent`) are **orchestrated** by Camunda Zeebe. The flows involve complex branching, long waiting periods (peer responses may arrive hours later), and high-stakes outcomes (a published cryptographic signature).

Peer reviewers are **humans**, not autonomous services. The process emails them a review link; they submit their determination via a REST callback that is correlated back to the waiting process instance using `verificationId` as a correlation key. This is **orchestration with asynchronous human-in-the-loop tasks**.

Choreography in our architecture is limited to the **Attestation Service** and **Notification Service** processes. Attestation Service runs on its own schedule and publishes `AttestationFailed` events to Kafka without instructions from an orchestrator. Consumers react independently — although we have not mapped out exactly what would happen. Perhaps it would start a new verification process. Notification service just subrscribes to relevant events.

---

## 2. ADR — Camunda 8 (Zeebe) vs. Camunda 7 / Operaton

### ADR-001: Workflow Engine Selection

**Status:** Accepted

**Context:**

The project requires a workflow engine to execute three BPMN processes with long-running state, message correlation, and timer events. We need to decide between Camunda 7 and Camunda 8.

**Decision:** Use **Camunda 8 (Zeebe)** with `pyzeebe` job workers.

**Justification:**

Our services are written in **Python using Flask**, not Java or Spring Boot. This is the single most consequential factor in the decision.

Camunda 7's primary integration model is Java delegates and Spring Boot embedded. External task workers are supported in C7 but are a secondary pattern. Zeebe's job worker model is the primary and idiomatic pattern, and `pyzeebe` (an asyncio-based Python library) provides a clean, well-maintained client. There is no equivalent for C7.



**Trade-offs accepted:**

- **Infrastructure overhead.** Running Camunda 8 self-managed requires Zeebe, Operate, and Elasticsearch (for process history). This is more infrastructure than an embedded Camunda 7 engine. We accept this because our services are already containerized with Docker. This is a pattern we are very familiar with.

- **Operate dependency for monitoring.** Camunda 8 does not expose a built-in history REST API in the same way C7 does. Operate (requiring Elasticsearch) is needed for process monitoring. For development and demo purposes this is acceptable.

- **Maturity of tooling.** Some Camunda 7 ecosystem features (complex BPMN simulations, decision tables via DMN embedded in processes) have less coverage in Zeebe. We do not use DMN in our current processes, so this is not a blocking concern.

---

## Technical Reference

- BPMN files: [`bpmn files/`](../bpmn%20files/)
- Service skeletons: [`services/`](../services/)
- Infrastructure: [`docker-compose.yml`](../docker-compose.yml)
- Exercise 3 submission (process design detail): [`submission-exercise-3.md`](./submission-exercise-3.md)
