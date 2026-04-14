# Submission - Exercise 3

- Course: Event-driven and Process-oriented Architectures (EDPO), FS2026, University of St.Gallen
- Group 4
  - Evan Martino
  - Marco Birchler
  - Roman Babukh

## Repository

- GitHub repository: <https://github.com/chechmek/EDPO_FS26_Project>

## Team Contribution

- Evan Martino refined the core concepts and BPMN processes, and made the documentation.  
- Marco Birchler created the orignal BPMN processes.

## Project Pivot

For Exercise 3, we pivoted from the social media simulation (Exercise 2) to a **content verification platform**. The new system allows users or organizations to submit web content (articles, pages, photos, etc.) for peer-based truth verification. A trusted third-party service coordinates the verification, collects verdicts from independent registered peers, and issues a cryptographic signature for content that passes. The platform also handles user registration and content reporting workflows.

This pivot allows us to focus more on the verification procesess, while purposely leaving the form and justification of the verified content outside of scope (e.g. we don't need to create a news platform, forum, or blog, etc.).

## Orchestration

The content verification flow has complex conditional branching (is the user registered? did the peers approve? did the internal check pass?), long waiting periods (peers may respond hours later), and multiple exception paths (timeout, rejection, unregistered user). For these reasons we orchestrated the verification, user registration, and reporting processes.



### 1. VerifyContent.bpmn

This is the primary orchestrated process. It handles the full lifecycle of a content verification request.

![](images/verification_process.png)


### 2. RegisterUser.bpmn

Handles onboarding of a new peer-verifier. Registration requires human background review before the user is admitted.

![](images/user_registration_process.png)

**Notable elements:**

- **Manual Task** (`Check User Background`): Models a step performed by a human outside the system — in this case a background/trustworthiness check with no system integration. Camunda tracks that the step must happen but does not automate it.
- **User Task** (`Fill out Form`): The applicant submits their details through a Camunda-rendered or custom HTML form. The task carries a Zeebe user task extension so it appears in the task list.
- **Intermediate Message Throw Events**: Both outcome paths end with a message throw, enabling downstream systems (e.g., an email notifier) to react to the registration outcome without being directly coupled to this process.

### 3. ReportContent.bpmn

Handles a user report against content that has already been published. The process gives the post owner a chance to object before any action is taken.

![](images/report_process.png)

**Notable elements:**

- **Event-based Gateway**: The system sends a notification to the post owner and then waits. If the owner raises an `Objection` message (via REST endpoint) before the timer fires, the case is escalated to manual review. If the deadline passes without a response, the post is deleted automatically.
- **Timer Event**: The deadline is a configurable duration (e.g., 72 hours) within Camunda.

## Service Architecture

The three BPMN processes are executed by three Python 3.12 / Flask microservices, each owning their own Zeebe job workers:

| Service | Port | BPMN process | Zeebe job types |
|---|---|---|---|
| `user-service` | 8001 | RegisterUser | `register-user`, `send-registration-notification` |
| `verification-service` | 8002 | VerifyContent | `check-user-registration`, `send-verification-request`, `internal-verification`, `publish-signature`, `send-verification-notification` |
| `reporting-service` | 8003 | ReportContent | `send-report-notification`, `delete-post` |
| `attestation-service` | 8004 | (none — scheduler) | — |

Each service starts its Zeebe job workers in a background thread alongside the Flask HTTP server. The `verification-service` also exposes `POST /verifications/<id>/peer-response` to receive verdicts from external peers, which it then correlates to the waiting Camunda process instance via `client.publish_message(name, correlation_key, variables)`.

## Attestation Service

In addition to the three orchestrated processes, the platform includes a standalone **Attestation Service** (port 8004). This service periodically re-fetches registered content URLs and compares their SHA-256 hash against the baseline captured at verification time. If the content has changed, it marks the attestation as `tampered` and publishes an `AttestationFailed` event to Kafka. This ensures that a published signature remains valid and that content drift is detected automatically.

## Technical Reference

- BPMN files: [`bpmn files/`](../bpmn%20files/)
- Service skeletons: [`services/`](../services/)
- Infrastructure: [`docker-compose.yml`](../docker-compose.yml)