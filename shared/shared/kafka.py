from __future__ import annotations

from confluent_kafka import Consumer, Producer

from shared.events import EventEnvelope


def build_producer(*, bootstrap_servers: str, client_id: str) -> Producer:
    return Producer(
        {
            "bootstrap.servers": bootstrap_servers,
            "client.id": client_id,
            "acks": "all",
            "enable.idempotence": True,
        }
    )


def build_consumer(
    *,
    bootstrap_servers: str,
    group_id: str,
    client_id: str,
    auto_offset_reset: str = "earliest",
) -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "client.id": client_id,
            "auto.offset.reset": auto_offset_reset,
            "enable.auto.commit": False,
        }
    )


def publish_event(
    producer: Producer,
    *,
    topic: str,
    key: str,
    envelope: EventEnvelope,
) -> None:
    producer.produce(topic=topic, key=key.encode("utf-8"), value=envelope.model_dump_json())
    producer.poll(0)
