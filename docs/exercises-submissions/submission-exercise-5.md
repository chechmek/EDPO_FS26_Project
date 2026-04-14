# Submission - Exercise 5

- Course: Event-driven and Process-oriented Architectures (EDPO), FS2026, University of St.Gallen
- Group 4
  - Evan Martino
  - Marco Birchler
  - Roman Babukh

## Repository

- GitHub repository: [https://github.com/chechmek/EDPO_FS26_Project](https://github.com/chechmek/EDPO_FS26_Project)

## Team Contribution

- Evan Martino worked on the implementation and deployment of the processes.
- Roman Babukh worked on the implementation of the non-process-related parts of the project.
- Marco Birchler worked on the documentation and submission.

---

## 2. Resilience Patterns in the Software Project

For our content verification platform we implemented two of the stateful resilience patterns introduced in the lecture and Lab08 (Flowing Retail): **Stateful Retry** and **Human Intervention**. Both patterns are modelled directly in BPMN and executed by Camunda Zeebe. Because Zeebe is an **event-sourced workflow engine** (Lecture 6), all changes to process state are recorded in an append-only event log and materialized into RocksDB snapshots. This means process variables survive crashes and redeployments without any additional persistence code on our side, which is the foundational property that makes stateful resilience patterns practical.

Zeebe also does not implement ACID transactions across services (Lecture 6, "Transactions and At-least-once Semantics"). Our patterns therefore rely on **state management and eventual consistency** rather than distributed transactions, consistent with the hint in the exercise sheet.

---

### 2.1 Stateful Retry: `VerifyContent.bpmn`

**Implementation:** In `VerifyContent.bpmn`, after `Send Verification Request to Peers` completes, an Event-Based Gateway waits for either a peer response message or a configurable timer. On timeout or service error, a Script Task increments `send_verify_retry` and an exclusive gateway routes back to retry or forward to a failure notification once the limit is reached.

Peer verification is the core value proposition of the platform: a cryptographic signature is only meaningful if it was issued after a genuine human review. Peers are humans who may be slow, offline, or simply unresponsive, so transient non-responses are expected and should not immediately cause a verification to fail.

As discussed in class, **stateful retry** differs fundamentally from a simple in-process retry: the retry state is owned by the workflow engine rather than the job worker. This matters here because the retry boundary spans an asynchronous wait that can last minutes. A job-worker retry would be reset by any crash during that window; a Zeebe process variable is not, because the engine reconstructs it from the event log on recovery. The retry count is also fully visible in **Camunda Operate** (via the CQRS exporter to Elasticsearch, Lecture 6), making the behaviour auditable. Capping retries at three prevents indefinite looping; after exhausting retries the requester receives a clear notification rather than a silent failure.

The main tradeoff is added process complexity: the retry loop, counter variable, and boundary error event all need to be modelled and tested explicitly. We accepted this cost because silently dropping a failed verification or failing on the first timeout would undermine trust in the platform.

---

### 2.2 Human Intervention: `ReportContent.bpmn`

**Implementation:** In `ReportContent.bpmn`, after the post owner is notified, an Event-Based Gateway waits for either an `Objection` message or a deadline timer. The objection message uses Zeebe's **message correlation** mechanism (Lecture 6): a subscription is registered on a partition determined by a hash of `reportId`, so the arriving message is reliably routed back to the correct waiting process instance. If an objection arrives, a `Review Objection` User Task is created in Camunda Tasklist for a moderator, who approves or rejects it via a rendered form. If the deadline fires with no objection, the post is deleted automatically.

Content moderation is an inherently subjective judgement that carries legal and reputational risk. As covered under **human intervention** in class, there are decisions that should not be fully automated: the consequences are irreversible (deletion) and the inputs are ambiguous (a contested report). By suspending the process at an Event-Based Gateway and giving the post owner a fixed window to raise an objection, we provide due process before anything is deleted. If an objection arrives, a moderator makes the final call rather than the system. This matches the human-in-the-loop pattern from the lecture, where a User Task pauses automated flow until a human provides a decision.

The tradeoff is that the process may stay open for the full deadline period before acting, but this is an intentional and explainable delay rather than a system failure.

---

## References

### Technical References

- BPMN files: [`bpmn files/`](../bpmn%20files/)
- Service skeletons: [`services/`](../services/)
- Infrastructure: [`docker-compose.yml`](../docker-compose.yml)
- Exercise 3 submission (process design detail): [`submission-exercise-3.md`](./submission-exercise-3.md)
- Exercise 4 submission (orchestration & ADR): [`submission-exercise-4.md`](./submission-exercise-4.md)

### Images of Processes and Forms

- VerifyContent process (stateful retry): [`images/E5/VerifyContent_E5.png`](./images/E5/VerifyContent_E5.png)
- ReportContent process (human intervention): [`images/E5/ReportContent_E5.png`](./images/E5/ReportContent_E5.png)
- RegisterUser process: [`images/E5/RegisterUser_E5.png`](./images/E5/RegisterUser_E5.png)
- Verify user: [`images/E5/VerifyUser_E5.png`](./images/E5/VerifyUser_E5.png)
- Check report validity: [`images/E5/CheckReportValidity_E5.png`](./images/E5/CheckReportValidity_E5.png)
- Review objection: [`images/E5/ReviewObjection_E5.png`](./images/E5/ReviewObjection_E5.png)