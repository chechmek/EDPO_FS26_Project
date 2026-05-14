package com.edpo.contentledger.model;

import java.time.Instant;
import java.util.Map;
import java.util.Objects;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * Immutable record of one decision in the content's timeline.
 * {@code eventId} is the dedupe key for at-least-once delivery semantics.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record Decision(
        String eventId,
        EventType eventType,
        String status,
        String actor,
        Instant eventTime,
        Instant ingestionTime,
        String sourceTopic,
        String correlationId,
        Map<String, Object> details
) {
    public Decision {
        Objects.requireNonNull(eventType, "eventType");
        // eventTime is filled by the aggregator with ingestion time if the source payload lacks one.
        Objects.requireNonNull(eventTime, "eventTime");
    }
}
