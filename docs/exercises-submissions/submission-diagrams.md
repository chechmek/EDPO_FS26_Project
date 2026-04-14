# Architecture Diagrams — EDPO Group 4 Content Verification Platform

- Course: Event-driven and Process-oriented Architectures (EDPO), FS2026, University of St.Gallen
- Group 4
  - Evan Martino
  - Marco Birchler
  - Roman Babukh

This document provides a set of conceptual-level diagrams covering the full implementation. The diagrams progress from the broad system view down to specific interaction patterns. Each is followed by a short explanation of what it shows and why it matters for the architecture.

---

## 1. System Context

The highest-level view: who interacts with the platform and through which channel. External actors on the left; platform services in the middle; shared infrastructure on the right.

```mermaid
flowchart LR
    subgraph Actors["External Actors"]
        U("User / Registrant")
        P("Peer Reviewer")
        PO("Post Owner")
        MOD("Moderator")
    end

    subgraph Platform["Platform Services"]
        US["user-service\n:8001"]
        VS["verification-service\n:8002"]
        RS["reporting-service\n:8003"]
        AS["attestation-service\n:8004"]
        NS["notification-service\n(Kafka-only)"]
    end

    subgraph Infra["Shared Infrastructure"]
        Z["Camunda 8 / Zeebe\n:26500 gRPC"]
        K["Apache Kafka\n:9092"]
        ES["Elasticsearch\n:9200"]
        OP["Camunda Operate\n(audit / monitoring)"]
        TL["Camunda Tasklist\n(human tasks)"]
    end

    U -->|"POST /users"| US
    U -->|"POST /verifications"| VS
    P -->|"POST /verifications/{id}/peer-response"| VS
    PO -->|"POST /reports/{id}/objection"| RS
    U -->|"POST /reports"| RS
    MOD -->|"complete task via UI"| TL

    VS -->|"GET /users/{id}"| US
    RS -->|"POST /attestations/invalidate"| AS
    VS -->|"POST /attestations"| AS

    US & VS & RS <-->|"gRPC job workers\n+ process start\n+ message publish"| Z
    Z -->|"event stream"| ES
    ES --> OP
    TL <-->|"user tasks"| Z

    US & VS & RS -->|"produce events\n(acks=all, idempotent)"| K
    K -->|"consume topics"| NS
```



**Reading notes:** All three orchestrated services (`user-service`, `verification-service`, `reporting-service`) maintain a bidirectional gRPC connection to Zeebe — they start processes, activate jobs, complete jobs, and publish messages over the same connection. The `attestation-service` is the only service with no Zeebe involvement; it is purely a REST-based signature store. The `notification-service` has no HTTP interface at all and only ever reads from Kafka.

---

## 2. Context Map (DDD)

The platform has four bounded contexts. This diagram shows their boundaries and the type of relationship between them, using standard DDD notation.

```mermaid
flowchart TB
    subgraph UserIdentity["User Identity Context\n(user-service)"]
        direction TB
        UR["RegisterUser.bpmn\n(Process_1kwkl0j)"]
        UD[("in-memory\nuser store")]
    end

    subgraph ContentVerification["Content Verification Context\n(verification-service)"]
        direction TB
        VC["VerifyContent.bpmn\n(Process_01gn4xr)"]
        VD[("in-memory\nverification store")]
    end

    subgraph ContentGovernance["Content Governance Context\n(reporting-service)"]
        direction TB
        RC["ReportContent.bpmn\n(Process_0rsygf3)"]
        RD[("in-memory\nreport / post store")]
    end

    subgraph Attestation["Attestation Context\n(attestation-service)"]
        direction TB
        AT["Signature Store\nREST API :8004"]
        AD[("in-memory\nsignature store")]
    end

    subgraph Notification["Notification Context\n(notification-service)"]
        NK["Kafka consumer\n6 topics"]
    end

    subgraph EventBus["Shared Kernel — Kafka Event Bus"]
        K1["user-registered\nuser-rejected"]
        K2["verification-notification"]
        K3["report-notification\npost-deleted\nobjection-approved"]
    end

    UserIdentity -->|"Customer / Supplier\nREST: GET /users/{id}"| ContentVerification
    ContentVerification -->|"Customer / Supplier\nREST: POST /attestations"| Attestation
    ContentGovernance -->|"Customer / Supplier\nREST: POST /attestations/invalidate"| Attestation

    UserIdentity -->|"Published Language\n(Kafka events)"| K1
    ContentVerification -->|"Published Language\n(Kafka events)"| K2
    ContentGovernance -->|"Published Language\n(Kafka events)"| K3

    K1 & K2 & K3 -->|"Conformist\n(reads events)"| Notification
```



**Reading notes:** The `User Identity` context is upstream of `Content Verification` — verifications depend on it to check eligibility, but the user service has no knowledge of verifications. The `Attestation` context is a shared capability used by both `Content Verification` (to store signatures) and `Content Governance` (to invalidate them), but it has no dependency on either. Kafka serves as a shared event bus for loosely coupled notifications following a Published Language pattern; the `notification-service` is a conformist consumer that adapts to whatever events the producing contexts emit.

---

## 3. Service Architecture and Communication Channels

This diagram makes the three different communication channels explicit: Zeebe gRPC (orchestration), synchronous REST (service-to-service calls), and Kafka (choreography).

```mermaid
flowchart TB
    subgraph ZeebeLayer["Orchestration Layer — Zeebe gRPC :26500"]
        P1["RegisterUser.bpmn"]
        P2["VerifyContent.bpmn"]
        P3["ReportContent.bpmn"]
    end

    subgraph AppLayer["Application Layer"]
        US["user-service :8001\nworkers: register-user, reject-user"]
        VS["verification-service :8002\nworkers: check-user-registration,\nsend-verification-request,\ninternal-verification,\npublish-signature,\nsend-verification-notification"]
        RS["reporting-service :8003\nworkers: send-report-notification,\ndelete-post, objection-approved,\npublish-post-deleted"]
        AS["attestation-service :8004\n(no Zeebe)"]
        NS["notification-service\n(no HTTP)"]
    end

    subgraph KafkaLayer["Choreography Layer — Kafka :9092"]
        T1["user-registered\nuser-rejected"]
        T2["verification-notification"]
        T3["report-notification\npost-deleted\nobjection-approved"]
    end

    US <-->|"gRPC\njob activation + completion\nprocess start"| P1
    VS <-->|"gRPC\njob activation + completion\nprocess start\nmessage publish"| P2
    RS <-->|"gRPC\njob activation + completion\nprocess start\nmessage publish"| P3

    VS -->|"REST GET /users/{id}"| US
    VS -->|"REST POST /attestations"| AS
    RS -->|"REST POST /attestations/invalidate"| AS

    US -->|"produce"| T1
    VS -->|"produce"| T2
    RS -->|"produce"| T3

    T1 & T2 & T3 -->|"consume\n(consumer group: notification-service)"| NS
```



**Reading notes:** Each service owns exactly the job types declared in its own BPMN model; there is no cross-service worker. The gRPC connection carries four distinct interaction types: job polling (`ZeebeWorker`), job completion, process instance launch (`ZeebeClient.run_process`), and message publication (`ZeebeClient.publish_message`). REST calls are used only for point-to-point queries where a synchronous answer is needed within a job handler — they are never used across process boundaries for coordination.

---

## 4. RegisterUser — Sequence

The user onboarding flow. Background verification is modelled as a human task in Tasklist, making the approval decision explicit, auditable, and not automated away.

```mermaid
sequenceDiagram
    actor User
    participant US as user-service
    participant Zeebe as Camunda Zeebe
    participant TL as Camunda Tasklist
    actor Moderator
    participant Kafka
    participant NS as notification-service

    User->>US: POST /users\n{username, password}
    US->>Zeebe: run_process(RegisterUser.bpmn)\n{userId, username, passwordHash,\nsimulateBackgroundPass}
    Zeebe-->>US: processInstanceKey
    US-->>User: 202 {userId, processInstanceKey}

    Note over Zeebe,TL: User Task: verify-user\n(moderator reviews background check)
    Zeebe->>TL: create User Task (verify-user form)
    Moderator->>TL: submit form {simulateBackgroundPass: true/false}
    TL->>Zeebe: complete User Task

    alt Background check passed
        Zeebe->>US: activate job: register-user
        US->>US: mark user registered=true, status="registered"
        US->>Kafka: produce → user-registered {userId}
        US->>Zeebe: complete job {userId, registered: true}
        Kafka->>NS: consume → log notification
    else Background check failed
        Zeebe->>US: activate job: reject-user
        US->>US: mark user registered=false, status="rejected"
        US->>Kafka: produce → user-rejected {userId}
        US->>Zeebe: complete job {userId, registered: false}
        Kafka->>NS: consume → log notification
    end
```



**Reading notes:** The `simulateBackgroundPass` flag lets us drive both paths in tests without requiring a real background-check integration. In production this would be replaced by a genuine third-party check result. The `userId` generated at process start is the stable identifier used in all downstream flows — it is the correlation between the identity context and the verification context.

---

## 5. VerifyContent — End-to-End Sequence

The full lifecycle of a content verification, from API call to Kafka notification. This is the most complex flow and demonstrates orchestration, message correlation, service-to-service REST, and Kafka choreography in a single trace.

```mermaid
sequenceDiagram
    actor Requester
    participant VS as verification-service
    participant Zeebe as Camunda Zeebe
    participant US as user-service
    participant ATT as attestation-service
    participant Kafka
    participant NS as notification-service

    Requester->>VS: POST /verifications\n{userId, contentUrl, peerMode}
    VS->>Zeebe: run_process(VerifyContent.bpmn)\n{verificationId, userId, contentUrl}
    Zeebe-->>VS: processInstanceKey
    VS-->>Requester: 202 {verificationId}

    Zeebe->>VS: activate job: check-user-registration
    VS->>US: GET /users/{userId}
    US-->>VS: {registered: true/false}
    VS->>Zeebe: complete job {userRegistered}

    alt userRegistered = false
        Zeebe->>VS: activate job: send-verification-notification
        VS->>Kafka: produce → verification-notification\n{status: rejected-unregistered}
        Kafka->>NS: consume → log notification
    else userRegistered = true
        Zeebe->>VS: activate job: send-verification-request
        VS->>Zeebe: complete job {peersSent, peerIds}

        Note over Zeebe: ⏸ Process suspended at Event-Based Gateway\n(awaits peer-approved / peer-rejected message\nor configurable timeout → stateful retry loop)

        actor Peer
        Peer->>VS: POST /verifications/{verificationId}/peer-response\n{peerId, approved: true}
        VS->>Zeebe: publish_message("peer-approved",\ncorrelationKey=verificationId)

        Zeebe->>VS: activate job: internal-verification
        VS->>Zeebe: complete job {internalCheckPassed}

        Zeebe->>VS: activate job: publish-signature
        VS->>ATT: POST /attestations\n{verificationId, signatureHash}
        ATT-->>VS: 201
        VS->>Zeebe: complete job {signatureId, signatureHash}

        Zeebe->>VS: activate job: send-verification-notification
        VS->>Kafka: produce → verification-notification\n{status: verified, signatureId}
        Kafka->>NS: consume → log notification
    end
```



**Reading notes:** The process instance is suspended at the Event-Based Gateway waiting for a peer message or a timeout. During that suspension, zero CPU or memory is consumed by the application — the state lives entirely in Zeebe's event-sourced log. The `verificationId` UUID is both the client-facing identifier (used in the REST URL) and the Zeebe correlation key, which eliminates any secondary routing table.

---

## 6. ReportContent — Human Intervention Sequence

The reporting flow demonstrates the human-in-the-loop pattern. A moderator's User Task gates the objection window, and a post owner's REST callback correlates back to the suspended instance.

```mermaid
sequenceDiagram
    actor Reporter
    participant RS as reporting-service
    participant Zeebe as Camunda Zeebe
    participant ATT as attestation-service
    participant TL as Camunda Tasklist
    actor Moderator
    actor PostOwner
    participant Kafka
    participant NS as notification-service

    Reporter->>RS: POST /reports\n{reporterId, postId, postOwnerId, reason}
    RS->>Zeebe: run_process(ReportContent.bpmn)\n{reportId, postId, postOwnerId}
    Zeebe-->>RS: processInstanceKey
    RS-->>Reporter: 202 {reportId}

    Note over Zeebe,TL: User Task: check-report-valid\n(moderator reviews report validity)
    Zeebe->>TL: create User Task (check-report-valid form)
    Moderator->>TL: submit form {report_valid: true}
    TL->>Zeebe: complete User Task

    Zeebe->>RS: activate job: send-report-notification
    RS->>Kafka: produce → report-notification\n{type: report-valid, userId: postOwnerId}
    RS->>Kafka: produce → report-notification\n{type: report-accepted, userId: reporterId}
    Kafka->>NS: consume → log notifications
    RS->>Zeebe: complete job

    Note over Zeebe: ⏸ Process suspended at Event-Based Gateway\n(awaits post-owner-objection message\nOR deadline timer)

    alt Post owner objects within deadline
        PostOwner->>RS: POST /reports/{reportId}/objection\n{explanation: "..."}
        RS->>Zeebe: publish_message("post-owner-objection",\ncorrelationKey=reportId)

        Note over Zeebe,TL: User Task: review-objection\n(moderator reviews the objection)
        Zeebe->>TL: create User Task (review-objection form)
        Moderator->>TL: submit form {objection_approved: true/false}
        TL->>Zeebe: complete User Task

        alt objection approved
            Zeebe->>RS: activate job: objection-approved
            RS->>Kafka: produce → objection-approved
            Kafka->>NS: consume → log notification
        else objection rejected → post deleted
            Zeebe->>RS: activate job: delete-post
            RS->>ATT: POST /attestations/invalidate {signatureId}
            ATT-->>RS: 200
            RS->>Kafka: produce → post-deleted
            Kafka->>NS: consume → log notification
        end
    else Deadline timer fires — no objection
        Zeebe->>RS: activate job: delete-post
        RS->>ATT: POST /attestations/invalidate {signatureId}
        ATT-->>RS: 200
        RS->>Kafka: produce → post-deleted
        Kafka->>NS: consume → log notification
    end
```



**Reading notes:** There are two distinct human tasks in this flow. The first (`check-report-valid`) is always required — no automation replaces the moderator's initial judgement. The second (`review-objection`) is conditional: it only fires if the post owner submits an objection within the deadline. Both User Tasks are visible in Camunda Tasklist with rendered forms, and both completions are recorded in Camunda Operate's audit history.

---

## Summary Table


| Diagram                   | Pattern / Concept                                        | ADR Reference      |
| ------------------------- | -------------------------------------------------------- | ------------------ |
| 1. System Context         | Overall platform boundary and actors                     | —                  |
| 2. Context Map            | Bounded contexts, upstream/downstream relationships      | ADR-0006           |
| 3. Service Architecture   | Communication channels: gRPC, REST, Kafka                | ADR-0001, ADR-0005 |
| 4. RegisterUser Sequence  | Human task onboarding, Kafka notification                | ADR-0003, ADR-0006 |
| 5. VerifyContent Sequence | Orchestration, message correlation, Kafka                | ADR-0007, ADR-0005 |
| 6. ReportContent Sequence | Human intervention, objection correlation, deletion saga | ADR-0003, ADR-0007 |



