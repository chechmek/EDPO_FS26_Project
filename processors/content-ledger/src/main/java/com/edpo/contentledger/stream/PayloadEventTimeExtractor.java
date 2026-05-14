package com.edpo.contentledger.stream;

import java.time.Instant;
import java.time.format.DateTimeParseException;

import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.streams.processor.TimestampExtractor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * Reads the business {@code eventTime} from the JSON payload (top-level or nested
 * inside {@code payload}) and uses it as the Kafka Streams record timestamp.
 *
 * This keeps the ledger event-time-correct, which is good hygiene for any later
 * windowed processing and for chronologically consistent trace replay. Falls back
 * to the Kafka record timestamp if no parseable {@code eventTime} is present.
 */
public class PayloadEventTimeExtractor implements TimestampExtractor {

    private static final Logger log = LoggerFactory.getLogger(PayloadEventTimeExtractor.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Override
    public long extract(ConsumerRecord<Object, Object> record, long partitionTime) {
        Object value = record.value();
        if (value == null) {
            return record.timestamp();
        }
        try {
            JsonNode root;
            if (value instanceof byte[] bytes) {
                root = MAPPER.readTree(bytes);
            } else if (value instanceof String s) {
                root = MAPPER.readTree(s);
            } else {
                return record.timestamp();
            }
            String eventTime = findEventTime(root);
            if (eventTime != null) {
                return Instant.parse(eventTime).toEpochMilli();
            }
        } catch (DateTimeParseException e) {
            log.debug("Unparseable eventTime in record, falling back to record timestamp", e);
        } catch (Exception e) {
            log.debug("Could not extract eventTime from record value, falling back to record timestamp", e);
        }
        return record.timestamp();
    }

    private static String findEventTime(JsonNode root) {
        if (root == null || root.isMissingNode()) return null;
        JsonNode top = root.get("eventTime");
        if (top != null && top.isTextual()) return top.asText();
        JsonNode payload = root.get("payload");
        if (payload != null && payload.isObject()) {
            JsonNode nested = payload.get("eventTime");
            if (nested != null && nested.isTextual()) return nested.asText();
        }
        return null;
    }
}
