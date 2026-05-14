package com.edpo.contentledger.model;

import java.time.Instant;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * Normalized cross-stream representation produced by {@code mapValues} on every input topic.
 * Carries enough metadata for the aggregator to assemble a full audit trace.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ContentEvent(
        String eventId,
        String contentId,
        EventType eventType,
        String status,
        String actor,
        Instant eventTime,
        String sourceTopic,
        String correlationId,
        Map<String, Object> details
) {
}
