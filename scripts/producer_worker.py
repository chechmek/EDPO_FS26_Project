from kafka import KafkaProducer
import time
import json
from config import *


def create_message(size):
    return {
        "data": "x" * size,
        "timestamp": time.time()
    }


def run_producer(topic, batch_size, acks):

    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        batch_size=batch_size,
        acks=acks,
        linger_ms=5,
        value_serializer=lambda v: json.dumps(v).encode("utf-8")
    )

    latencies = []

    start_time = time.time()

    for _ in range(MESSAGE_COUNT):

        message = create_message(MESSAGE_SIZE)

        send_start = time.time()

        future = producer.send(topic, message)
        future.get()  # wait for broker ack

        latency = time.time() - send_start
        latencies.append(latency)

    producer.flush()

    total_time = time.time() - start_time

    return {
        "throughput": MESSAGE_COUNT / total_time,
        "avg_latency": sum(latencies) / len(latencies)
    }