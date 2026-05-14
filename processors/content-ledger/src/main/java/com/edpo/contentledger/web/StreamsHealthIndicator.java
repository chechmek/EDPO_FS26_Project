package com.edpo.contentledger.web;

import org.apache.kafka.streams.KafkaStreams;
import org.springframework.boot.actuate.health.Health;
import org.springframework.boot.actuate.health.HealthIndicator;
import org.springframework.kafka.config.StreamsBuilderFactoryBean;
import org.springframework.stereotype.Component;

/**
 * Integrates Kafka Streams runtime state into Spring Boot Actuator's {@code /actuator/health}.
 * Orchestrators (Compose, Kubernetes) can pick this up as a single source of truth for readiness
 * instead of polling the bespoke {@code /api/health/stream} endpoint.
 */
@Component("streams")
public class StreamsHealthIndicator implements HealthIndicator {

    private final StreamsBuilderFactoryBean factory;

    public StreamsHealthIndicator(StreamsBuilderFactoryBean factory) {
        this.factory = factory;
    }

    @Override
    public Health health() {
        KafkaStreams streams = factory.getKafkaStreams();
        if (streams == null) {
            return Health.down().withDetail("state", "NOT_INITIALIZED").build();
        }
        KafkaStreams.State state = streams.state();
        Health.Builder builder = state.isRunningOrRebalancing() ? Health.up() : Health.down();
        return builder
                .withDetail("state", state.name())
                .withDetail("running", state.isRunningOrRebalancing())
                .build();
    }
}
