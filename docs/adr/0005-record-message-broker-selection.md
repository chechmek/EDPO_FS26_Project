# 0005 - Message Broker Selection: Apache Kafka for Choreography Events

14.04.2026

## Status

Accepted

## Context

As described in [ADR-0001](./0001-record-coordination-pattern.md), we use choreography for notification-style side-effects: events like `verification-notification`, `report-notification`, `user-registered`, `user-rejected`, `post-deleted`, and `objection-approved`. These events are fire-and-forget from the producer's perspective — the `verification-service` publishes a `verification-notification` event after a verification outcome is determined, and it neither knows nor cares how many consumers receive it.

We need a broker that reliably delivers these events, can handle situations where a consumer is temporarily unavailable (without the producer having to retry), and allows us to add new consumers in the future without touching existing producers or reconfiguring the broker topology.

The two broad categories we considered were traditional message queues — represented by RabbitMQ — and log-based streaming platforms, represented by Apache Kafka.

Traditional MQ systems like RabbitMQ use a push model where the broker routes messages to registered consumers and removes them once acknowledged. They are easy to operate and well-suited to task queues. However, they are less well-suited to the pub/sub, multi-consumer fanout we need: adding a second consumer to a RabbitMQ queue means either duplicating the queue (and updating the producer to publish to both) or using an exchange — either way, there is coordination required when adding consumers. Message retention is also bounded; a message not consumed within a TTL is gone.

Kafka uses a pull model built on an append-only, partitioned log. Consumers maintain their own offsets and read at their own pace. Adding a new consumer group changes nothing for existing producers or existing consumers. Messages are retained for a configurable period regardless of whether any consumer has read them, which provides a natural buffer against consumer downtime and also makes replaying past events straightforward.

### Decision Drivers

- Extensibility: new consumers must be addable without modifying existing producers or reconfiguring the broker
- Reliability: messages must survive consumer downtime without requiring producer-side retry logic
- Scalability: the broker must support horizontal scaling of consumers via partitioning
- Elasticity: individual consumers must be independently scalable

### Options Considered

**Direct HTTP calls from producer to each consumer:** no broker required. Pros: simple, easy to reason about. Cons: producers must know all consumers; adding a new consumer requires changing producer code; no buffering for consumer downtime.

**Traditional MQ (RabbitMQ):** push-based, broker routes messages to registered consumers. Pros: easy to operate, low memory overhead. Cons: adding a new consumer requires binding new queues or exchanges, a configuration change that must be coordinated with the existing deployment; messages are dropped after their TTL if unconsumed.

**Apache Kafka (chosen):** append-only, partitioned log with consumer-managed offsets. Pros: consumers are fully independent of each other and of producers, configurable message retention survives consumer downtime, horizontal scaling via consumer groups, idempotent producer configuration prevents duplicates. Cons: significantly heavier to operate locally than a simple queue.

## Decision

We use Apache Kafka (exposed at port `9092` via Docker Compose, internal broker address `kafka:29092`) as the message broker for all choreography-style event flows. Producers are configured with `acks=all` and `enable.idempotence=True` to avoid duplicate or lost messages under retries. The `notification-service` is a Kafka consumer using a named consumer group (`notification-service`) with manual offset commit after successful message processing.

The `verification-service` and `reporting-service` each create a `confluent-kafka` `Producer` instance at startup (lazily, on first use). The `notification-service` creates a `Consumer` that subscribes to all six notification topics in a single call. Kafka's partition-based routing means the `notification-service` can be scaled horizontally — multiple instances can share the same consumer group and Kafka will distribute the partitions between them automatically.

We deliberately chose not to use RabbitMQ or another traditional MQ. The main reason is the consumer-independence property. We expect to add more consumers to these topics as the platform grows (an analytics service, a billing service, an email gateway). With Kafka, this is a matter of deploying a new consumer with its own group ID and subscribing to the relevant topics. With RabbitMQ the same expansion would require binding new queues to existing exchanges, a configuration change that would need to be coordinated with the existing deployment.

## Consequences

### Positive Consequences

The `notification-service` is entirely decoupled from the services that produce events. A crash in the notification service does not affect the `verification-service` or `reporting-service` at all; they produce their events to the log and continue. When the notification service restarts, it reads from its last committed offset and processes any messages it missed during the outage. No messages are dropped. New consumers can subscribe to existing topics without any changes to producers or broker configuration. Messages are retained through consumer downtime. The `notification-service` is horizontally scalable via consumer group partitioning. Idempotent producers prevent duplicate events under retry conditions. Kafka's log retention also gives us a short-term audit trail of notification events, viewable through the Kafka UI at port `8079`, which is useful during development.

### Negative Consequences

Kafka is significantly heavier than RabbitMQ to run locally, and its configuration surface (broker settings, topic creation, consumer group behaviour) requires more upfront understanding. For the current scale of five services and six topics with moderate throughput, Kafka is somewhat over-engineered. The `acks=all` producer configuration means each produce call waits for all in-sync replicas to acknowledge the write. In a single-broker development setup this effectively means waiting for the broker, which adds a small amount of latency to each event publication. Schema management across topics (currently informal JSON) could also become a maintenance concern as the topic count grows, and debugging consumer lag and offset issues requires familiarity with Kafka tooling.