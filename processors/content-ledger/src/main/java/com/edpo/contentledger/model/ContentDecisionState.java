package com.edpo.contentledger.model;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * Aggregated state for one {@code contentId}.
 *
 * Stored in a Kafka Streams KTable (RocksDB + changelog).
 * The decision trace is kept in chronological order by {@code eventTime},
 * and duplicate events are ignored via {@code seenEventIds}.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class ContentDecisionState {

    private String contentId;
    private LifecycleStatus lifecycleStatus = LifecycleStatus.NEW;
    private String lastVerificationStatus;
    private String lastReportStatus;
    private boolean deleted;
    private boolean restored;
    private Instant firstSeenAt;
    private Instant lastUpdatedAt;
    private long decisionCount;
    private List<Decision> decisionTrace = new ArrayList<>();
    private Set<String> seenEventIds = new LinkedHashSet<>();

    public ContentDecisionState() {}

    public boolean apply(ContentEvent event, Instant now) {
        if (event == null) {
            return false;
        }
        if (event.eventId() != null && !seenEventIds.add(event.eventId())) {
            return false;
        }
        if (this.contentId == null) {
            this.contentId = event.contentId();
            this.firstSeenAt = event.eventTime() != null ? event.eventTime() : now;
        }

        Instant effectiveEventTime = event.eventTime() != null ? event.eventTime() : now;
        Decision decision = new Decision(
                event.eventId(),
                event.eventType(),
                event.status(),
                event.actor(),
                effectiveEventTime,
                now,
                event.sourceTopic(),
                event.correlationId(),
                event.details()
        );

        insertSorted(decision);
        decisionCount = decisionTrace.size();
        lastUpdatedAt = now;

        // Normalize the status once so all downstream matching is case-insensitive and trim-safe.
        String normalizedStatus = event.status() == null
                ? ""
                : event.status().trim().toLowerCase();

        switch (event.eventType()) {
            case VERIFICATION -> {
                this.lastVerificationStatus = event.status();
                if (normalizedStatus.equals("verified")) {
                    if (lifecycleStatus == LifecycleStatus.NEW
                            || lifecycleStatus == LifecycleStatus.REJECTED) {
                        lifecycleStatus = LifecycleStatus.VERIFIED;
                    }
                } else if (normalizedStatus.startsWith("rejected")) {
                    if (lifecycleStatus == LifecycleStatus.NEW
                            || lifecycleStatus == LifecycleStatus.VERIFIED) {
                        lifecycleStatus = LifecycleStatus.REJECTED;
                    }
                }
            }
            case REPORT -> {
                this.lastReportStatus = event.status();
                boolean reportAcceptedOrValid = normalizedStatus.startsWith("report-accepted")
                        || normalizedStatus.equals("report-valid");
                if (reportAcceptedOrValid) {
                    if (!deleted) {
                        lifecycleStatus = LifecycleStatus.REPORTED_OPEN;
                    }
                } else if (normalizedStatus.equals("report-dismissed")) {
                    if (lifecycleStatus == LifecycleStatus.REPORTED_OPEN) {
                        lifecycleStatus = LifecycleStatus.REPORT_DISMISSED;
                    }
                }
            }
            case DELETION -> {
                this.deleted = true;
                this.lifecycleStatus = LifecycleStatus.DELETED;
            }
            case OBJECTION_APPROVED -> {
                // Only treat as a restoration if the content was actually deleted before.
                // An out-of-order objection-before-deletion still gets recorded in the trace
                // but must not flip lifecycle to RESTORED without a prior DELETION.
                if (deleted || lifecycleStatus == LifecycleStatus.DELETED) {
                    this.deleted = false;
                    this.restored = true;
                    this.lifecycleStatus = LifecycleStatus.RESTORED;
                }
            }
        }
        return true;
    }

    private void insertSorted(Decision decision) {
        int idx = Collections.binarySearch(
                decisionTrace,
                decision,
                (a, b) -> a.eventTime().compareTo(b.eventTime())
        );
        if (idx < 0) {
            idx = -(idx + 1);
        } else {
            while (idx < decisionTrace.size()
                    && decisionTrace.get(idx).eventTime().equals(decision.eventTime())) {
                idx++;
            }
        }
        decisionTrace.add(idx, decision);
    }

    public String getContentId() { return contentId; }
    public void setContentId(String v) { this.contentId = v; }
    public LifecycleStatus getLifecycleStatus() { return lifecycleStatus; }
    public void setLifecycleStatus(LifecycleStatus v) { this.lifecycleStatus = v; }
    public String getLastVerificationStatus() { return lastVerificationStatus; }
    public void setLastVerificationStatus(String v) { this.lastVerificationStatus = v; }
    public String getLastReportStatus() { return lastReportStatus; }
    public void setLastReportStatus(String v) { this.lastReportStatus = v; }
    public boolean isDeleted() { return deleted; }
    public void setDeleted(boolean v) { this.deleted = v; }
    public boolean isRestored() { return restored; }
    public void setRestored(boolean v) { this.restored = v; }
    public Instant getFirstSeenAt() { return firstSeenAt; }
    public void setFirstSeenAt(Instant v) { this.firstSeenAt = v; }
    public Instant getLastUpdatedAt() { return lastUpdatedAt; }
    public void setLastUpdatedAt(Instant v) { this.lastUpdatedAt = v; }
    public long getDecisionCount() { return decisionCount; }
    public void setDecisionCount(long v) { this.decisionCount = v; }
    public List<Decision> getDecisionTrace() { return decisionTrace; }
    public void setDecisionTrace(List<Decision> v) { this.decisionTrace = v == null ? new ArrayList<>() : v; }
    public Set<String> getSeenEventIds() { return seenEventIds; }
    public void setSeenEventIds(Set<String> v) { this.seenEventIds = v == null ? new LinkedHashSet<>() : v; }
}
