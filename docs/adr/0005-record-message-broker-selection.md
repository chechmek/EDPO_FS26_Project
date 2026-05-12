# 0005 - Message Broker Selection: Apache Kafka for Choreography Events

## Status

Accepted

## Context

As described in [ADR-0001](./0001-record-coordination-pattern.md), we use choreography with events for side-effects. We need a broker that reliably delivers these events, can handle situations where a consumer is temporarily unavailable (without the producer having to retry), and allows us to add new consumers in the future without touching existing producers.

The two broad categories we considered were traditional message queues — represented by RabbitMQ — and log-based streaming platforms, represented by Apache Kafka.

Traditional MQ systems like RabbitMQ use a push model where the broker routes messages to registered consumers and removes them once acknowledged. They are easy to operate and well-suited to task queues. However, they are less well-suited to the pub/sub, multi-consumer fanout we need: adding a second consumer to a RabbitMQ queue means either duplicating the queue (and updating the producer to publish to both) or using an exchange — either way, there is coordination required when adding consumers. Message retention is also bounded; a message not consumed within a TTL is gone.

Kafka uses a pull model built on an append-only, partitioned log. Consumers maintain their own offsets and read at their own pace. Adding a new consumer group changes nothing for existing producers or existing consumers. Messages are retained for a configurable period regardless of whether any consumer has read them, which provides a natural buffer against consumer downtime and also makes replaying past events straightforward.

### Options Considered

**Direct HTTP calls from producer to each consumer:** no broker required. Pros: simple, easy to reason about. Cons: producers must know all consumers; adding a new consumer requires changing producer code; no buffering for consumer downtime.

**Traditional MQ (RabbitMQ):** push-based, broker routes messages to registered consumers. Pros: easy to operate, low memory overhead. Cons: adding a new consumer requires binding new queues or exchanges, a configuration change that must be coordinated with the existing deployment; messages are dropped after their TTL if unconsumed.

**Apache Kafka (chosen):** append-only, partitioned log with consumer-managed offsets. Pros: consumers are fully independent of each other and of producers, configurable message retention survives consumer downtime, horizontal scaling via consumer groups, idempotent producer configuration prevents duplicates. Cons: significantly heavier to operate locally than a simple queue.

## Decision

We use Apache Kafka as the message broker for all choreography-style event flows.  The `notification-service` is a Kafka consumer using a named consumer group (`notification-service`) with manual offset commit after successful message processing.

The `verification-service` and `reporting-service` each create a `confluent-kafka` `Producer` instance at startup. The `notification-service` creates a `Consumer` that subscribes to all six notification topics in a single call. Kafka's partition-based routing means the `notification-service` can be scaled horizontally — multiple instances can share the same consumer group and Kafka will distribute the partitions between them automatically.
