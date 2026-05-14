package com.edpo.contentledger.stream;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.edpo.contentledger.model.ContentEvent;
import com.edpo.contentledger.model.EventType;
import com.fasterxml.jackson.databind.JsonNode;

/**
 * Maps the four input topics to a unified {@link ContentEvent}. Tolerates legacy payloads
 * (without {@code contentId}/{@code eventTime}/{@code eventId}) by falling back to
 * post-id / record-time / generated UUID so the topology never drops events.
 */
public final class EventNormalizer {

    private static final Logger log = LoggerFactory.getLogger(EventNormalizer.class);

    private EventNormalizer() {}

    public static ContentEvent fromVerification(JsonNode root) {
        if (root == null) return null;
        JsonNode payload = root.path("payload");
        String contentId = firstString(root.get("contentId"), payload.get("contentId"),
                payload.get("contentUrl"), payload.get("verificationId"));
        if (contentId == null) {
            log.warn("verification-notification without contentId; dropping");
            return null;
        }
        String rawStatus = firstString(
                payload.get("status"),
                root.get("status"),
                root.get("type"),
                payload.get("type"));
        String status = canonicalVerificationStatus(rawStatus);
        String userId = firstString(root.get("userId"), payload.get("userId"));
        String correlationId = firstString(payload.get("verificationId"), root.get("verificationId"));
        Map<String, Object> details = detailsFrom(payload, "signatureId", "contentUrl", "contentTitle");
        if (root.hasNonNull("message")) details.put("message", root.get("message").asText());
        return new ContentEvent(
                eventId(root, payload, "verification", correlationId, status),
                contentId,
                EventType.VERIFICATION,
                status,
                userId,
                eventTime(root, payload),
                "verification-notification",
                correlationId,
                details
        );
    }

    public static ContentEvent fromReport(JsonNode root) {
        if (root == null) return null;
        JsonNode payload = root.path("payload");
        String contentId = firstString(root.get("contentId"), payload.get("contentId"),
                payload.get("postId"), root.get("postId"));
        if (contentId == null) {
            log.warn("report-notification without contentId/postId; dropping");
            return null;
        }
        String status = firstString(root.get("type"), payload.get("type"));
        String correlationId = firstString(payload.get("reportId"), root.get("reportId"));
        String userId = firstString(root.get("userId"), payload.get("reporterId"));
        Map<String, Object> details = detailsFrom(payload, "postId", "reason");
        if (root.hasNonNull("message")) details.put("message", root.get("message").asText());
        return new ContentEvent(
                eventId(root, payload, "report", correlationId, status),
                contentId,
                EventType.REPORT,
                status,
                userId,
                eventTime(root, payload),
                "report-notification",
                correlationId,
                details
        );
    }

    public static ContentEvent fromPostDeleted(JsonNode root) {
        if (root == null) return null;
        String contentId = firstString(root.get("contentId"), root.get("postId"));
        if (contentId == null) {
            log.warn("post-deleted without contentId/postId; dropping");
            return null;
        }
        // Prefer the top-level status set by the producer (e.g. "post-deleted") so the trace
        // exposes the same domain status string that producers put on the wire.
        String status = firstString(root.get("status"));
        if (status == null) status = "deleted";
        String correlationId = firstString(root.get("reportId"));
        String userId = firstString(root.get("postOwnerId"));
        Map<String, Object> details = detailsFrom(root, "postId", "signatureId",
                "signatureInvalidated", "signatureInvalidatedCount");
        return new ContentEvent(
                eventId(root, null, "deletion", correlationId, status),
                contentId,
                EventType.DELETION,
                status,
                userId,
                eventTime(root, null),
                "post-deleted",
                correlationId,
                details
        );
    }

    public static ContentEvent fromObjectionApproved(JsonNode root) {
        if (root == null) return null;
        String contentId = firstString(root.get("contentId"), root.get("postId"));
        if (contentId == null) {
            log.warn("objection-approved without contentId/postId; dropping");
            return null;
        }
        String status = firstString(root.get("status"));
        if (status == null) status = "approved";
        String correlationId = firstString(root.get("reportId"));
        String userId = firstString(root.get("postOwnerId"));
        Map<String, Object> details = detailsFrom(root, "postId");
        return new ContentEvent(
                eventId(root, null, "objection", correlationId, status),
                contentId,
                EventType.OBJECTION_APPROVED,
                status,
                userId,
                eventTime(root, null),
                "objection-approved",
                correlationId,
                details
        );
    }

    /**
     * Map any verification status flavor — {@code verified}, {@code verification-verified},
     * {@code rejected}, {@code rejected-low-quality}, {@code verification-rejected}, ... —
     * onto a canonical Lifecycle token: {@code "verified"} or {@code "rejected[-<reason>]"}.
     * Unknown values are returned unchanged so the trace remains lossless.
     */
    static String canonicalVerificationStatus(String raw) {
        if (raw == null) return null;
        String normalized = raw.trim().toLowerCase();
        int rejIdx = normalized.indexOf("rejected");
        if (rejIdx >= 0) {
            return normalized.substring(rejIdx);
        }
        if (normalized.contains("verified")) {
            return "verified";
        }
        return raw;
    }

    private static String firstString(JsonNode... nodes) {
        for (JsonNode n : nodes) {
            if (n != null && !n.isMissingNode() && !n.isNull()) {
                String s = n.asText();
                if (s != null && !s.isBlank()) return s;
            }
        }
        return null;
    }

    private static Map<String, Object> detailsFrom(JsonNode source, String... keys) {
        Map<String, Object> details = new HashMap<>();
        if (source == null || source.isMissingNode()) return details;
        for (String key : keys) {
            JsonNode n = source.get(key);
            if (n == null || n.isNull() || n.isMissingNode()) continue;
            if (n.isBoolean()) details.put(key, n.asBoolean());
            else if (n.isInt() || n.isLong()) details.put(key, n.asLong());
            else if (n.isFloatingPointNumber()) details.put(key, n.asDouble());
            else details.put(key, n.asText());
        }
        return details;
    }

    private static Instant eventTime(JsonNode root, JsonNode payload) {
        String raw = firstString(
                root != null ? root.get("eventTime") : null,
                payload != null ? payload.get("eventTime") : null
        );
        if (raw != null) {
            try {
                return Instant.parse(raw);
            } catch (DateTimeParseException ignore) {
                // fall through
            }
        }
        // Returning null is deterministic: the aggregator fills it with the ingestion time once,
        // and {@code PayloadEventTimeExtractor} still drives Kafka Streams' record-time using
        // the record timestamp. Using Instant.now() here would silently break replay determinism.
        return null;
    }

    private static String eventId(JsonNode root, JsonNode payload, String kind,
                                   String correlationId, String status) {
        String id = firstString(
                root != null ? root.get("eventId") : null,
                payload != null ? payload.get("eventId") : null
        );
        if (id != null) return id;
        // Stable synthetic id is replay-safe; fall back to a content-hash of the raw envelope
        // so reprocessing the same input always yields the same id (no UUIDs).
        if (correlationId != null && status != null) {
            return "synthetic:" + String.join("|", kind, correlationId, status);
        }
        String envelope = (root == null ? "" : root.toString())
                + "|" + (payload == null ? "" : payload.toString());
        return "hash:" + kind + ":" + sha256Short(envelope);
    }

    private static String sha256Short(String input) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] hash = md.digest(input.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(hash).substring(0, 16);
        } catch (NoSuchAlgorithmException e) {
            return Integer.toHexString(input.hashCode());
        }
    }
}
