package com.edpo.contentledger.stream;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import java.util.Properties;

import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.apache.kafka.streams.StreamsBuilder;
import org.apache.kafka.streams.StreamsConfig;
import org.apache.kafka.streams.TestInputTopic;
import org.apache.kafka.streams.TestOutputTopic;
import org.apache.kafka.streams.Topology;
import org.apache.kafka.streams.TopologyTestDriver;
import org.apache.kafka.streams.state.KeyValueStore;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import com.edpo.contentledger.config.ContentLedgerProperties;
import com.edpo.contentledger.model.ContentDecisionState;
import com.edpo.contentledger.model.EventType;
import com.edpo.contentledger.model.LifecycleStatus;
import com.edpo.contentledger.serde.JsonSerde;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;

class ContentLedgerTopologyTest {

    private TopologyTestDriver driver;
    private TestInputTopic<String, String> verification;
    private TestInputTopic<String, String> report;
    private TestInputTopic<String, String> deleted;
    private TestInputTopic<String, String> objection;
    private TestOutputTopic<String, String> output;
    private KeyValueStore<String, ContentDecisionState> store;
    private ObjectMapper mapper;

    @BeforeEach
    void setUp() {
        mapper = new ObjectMapper().registerModule(new JavaTimeModule());
        ContentLedgerProperties props = new ContentLedgerProperties();
        props.getTopics().setVerificationNotification("verification-notification");
        props.getTopics().setReportNotification("report-notification");
        props.getTopics().setPostDeleted("post-deleted");
        props.getTopics().setObjectionApproved("objection-approved");
        props.getTopics().setOutput("content-decision-ledger");
        props.getStore().setContentState("content-state-store");

        ContentLedgerTopology builder = new ContentLedgerTopology(props, mapper);
        JsonSerde<com.edpo.contentledger.model.ContentEvent> eventSerde = builder.contentEventSerde();
        JsonSerde<ContentDecisionState> stateSerde = builder.contentStateSerde();
        Topology topology = builder.kafkaStreamsTopology(new StreamsBuilder(), eventSerde, stateSerde);

        Properties cfg = new Properties();
        cfg.put(StreamsConfig.APPLICATION_ID_CONFIG, "content-ledger-test");
        cfg.put(StreamsConfig.BOOTSTRAP_SERVERS_CONFIG, "dummy:9092");
        cfg.put(StreamsConfig.DEFAULT_KEY_SERDE_CLASS_CONFIG, Serdes.String().getClass().getName());
        cfg.put(StreamsConfig.DEFAULT_VALUE_SERDE_CLASS_CONFIG, Serdes.String().getClass().getName());
        cfg.put(StreamsConfig.DEFAULT_TIMESTAMP_EXTRACTOR_CLASS_CONFIG, PayloadEventTimeExtractor.class.getName());

        driver = new TopologyTestDriver(topology, cfg);

        verification = driver.createInputTopic("verification-notification",
                new StringSerializer(), new StringSerializer());
        report = driver.createInputTopic("report-notification",
                new StringSerializer(), new StringSerializer());
        deleted = driver.createInputTopic("post-deleted",
                new StringSerializer(), new StringSerializer());
        objection = driver.createInputTopic("objection-approved",
                new StringSerializer(), new StringSerializer());
        output = driver.createOutputTopic("content-decision-ledger",
                new StringDeserializer(), new StringDeserializer());

        store = driver.getKeyValueStore("content-state-store");
    }

    @AfterEach
    void tearDown() {
        if (driver != null) driver.close();
    }

    @Test
    void verificationThenReportThenDeletionUpdatesLifecycle() throws Exception {
        String contentId = "post-42";
        verification.pipeInput("v-1", verificationJson(contentId, "verified",
                "2026-05-12T20:00:00Z", "v-1"));
        report.pipeInput("r-1", reportJson(contentId, "report-accepted",
                "2026-05-12T20:01:00Z", "r-1"));
        deleted.pipeInput(contentId, postDeletedJson(contentId,
                "2026-05-12T20:02:00Z", "r-1"));

        ContentDecisionState state = store.get(contentId);
        assertThat(state).isNotNull();
        assertThat(state.getLifecycleStatus()).isEqualTo(LifecycleStatus.DELETED);
        assertThat(state.isDeleted()).isTrue();
        assertThat(state.getDecisionTrace()).hasSize(3);
        assertThat(state.getDecisionTrace())
                .extracting("eventType")
                .containsExactly(EventType.VERIFICATION, EventType.REPORT, EventType.DELETION);
    }

    @Test
    void objectionApprovedRestoresContent() throws Exception {
        String contentId = "post-7";
        verification.pipeInput("v-7", verificationJson(contentId, "verified",
                "2026-05-12T20:00:00Z", "v-7"));
        deleted.pipeInput(contentId, postDeletedJson(contentId,
                "2026-05-12T20:05:00Z", "r-7"));
        objection.pipeInput("r-7", objectionJson(contentId,
                "2026-05-12T20:10:00Z", "r-7"));

        ContentDecisionState state = store.get(contentId);
        assertThat(state.getLifecycleStatus()).isEqualTo(LifecycleStatus.RESTORED);
        assertThat(state.isDeleted()).isFalse();
        assertThat(state.isRestored()).isTrue();
        assertThat(state.getDecisionTrace()).hasSize(3);
    }

    @Test
    void duplicateEventIdIsIdempotent() throws Exception {
        String contentId = "post-dup";
        String json = verificationJson(contentId, "verified",
                "2026-05-12T20:00:00Z", "v-dup", "stable-event-id");
        verification.pipeInput("v-dup", json);
        verification.pipeInput("v-dup", json);

        ContentDecisionState state = store.get(contentId);
        assertThat(state.getDecisionTrace()).hasSize(1);
    }

    @Test
    void outOfOrderEventsAreSortedByEventTime() throws Exception {
        String contentId = "post-ooo";
        deleted.pipeInput(contentId, postDeletedJson(contentId,
                "2026-05-12T20:10:00Z", "r-ooo"));
        verification.pipeInput("v-ooo", verificationJson(contentId, "verified",
                "2026-05-12T20:00:00Z", "v-ooo"));

        ContentDecisionState state = store.get(contentId);
        assertThat(state.getDecisionTrace())
                .extracting("eventType")
                .containsExactly(EventType.VERIFICATION, EventType.DELETION);
    }

    @Test
    void emitsToOutputTopic() throws Exception {
        verification.pipeInput("v-x", verificationJson("post-x", "verified",
                "2026-05-12T20:00:00Z", "v-x"));
        assertThat(output.readKeyValuesToList()).isNotEmpty();
    }

    @Test
    void objectionWithoutDeletionDoesNotRestore() throws Exception {
        String contentId = "post-no-del";
        verification.pipeInput("v-nd", verificationJson(contentId, "verified",
                "2026-05-12T20:00:00Z", "v-nd"));
        objection.pipeInput("r-nd", objectionJson(contentId,
                "2026-05-12T20:01:00Z", "r-nd"));

        ContentDecisionState state = store.get(contentId);
        assertThat(state.getLifecycleStatus()).isEqualTo(LifecycleStatus.VERIFIED);
        assertThat(state.isRestored()).isFalse();
        assertThat(state.isDeleted()).isFalse();
        // The objection event is still recorded in the trace for full auditability.
        assertThat(state.getDecisionTrace())
                .extracting("eventType")
                .containsExactly(EventType.VERIFICATION, EventType.OBJECTION_APPROVED);
    }

    @Test
    void mixedCaseAndPaddedStatusesAreNormalized() throws Exception {
        String contentId = "post-mc";
        // Padded + uppercase verification status
        String json = mapToJson(java.util.Map.of(
                "userId", "user-mc",
                "type", "verification-verified",
                "message", "ok",
                "eventTime", "2026-05-12T20:00:00Z",
                "payload", java.util.Map.of(
                        "verificationId", "v-mc",
                        "contentId", contentId,
                        "contentUrl", contentId,
                        "status", "  VERIFIED ",
                        "eventId", "ev-mc"
                )));
        verification.pipeInput("v-mc", json);

        ContentDecisionState state = store.get(contentId);
        assertThat(state.getLifecycleStatus()).isEqualTo(LifecycleStatus.VERIFIED);
    }

    @Test
    void producerWireFormatIsAccepted() throws Exception {
        // Pins the exact payload shape produced by the upstream Python services so that
        // any breaking change in the wire format is caught before it breaks the ledger.
        String contentId = "content-abcdef1234567890";  // matches sha256 prefix convention

        String verifJson = mapToJson(java.util.Map.of(
                "eventTime", "2026-05-13T20:00:00Z",
                "userId", "user-1",
                "contentId", contentId,
                "status", "verified",
                "type", "verification-verified",
                "message", "Verification completed",
                "payload", java.util.Map.of(
                        "verificationId", "v-team",
                        "contentId", contentId,
                        "status", "verified",
                        "signatureId", "sig-1"
                )));
        verification.pipeInput("v-team", verifJson);

        String reportJson = mapToJson(java.util.Map.of(
                "eventTime", "2026-05-13T20:01:00Z",
                "userId", "reporter-1",
                "contentId", contentId,
                "status", "report-accepted",
                "type", "report-accepted",
                "message", "Report accepted",
                "payload", java.util.Map.of(
                        "reportId", "r-team",
                        "postId", contentId,
                        "contentId", contentId
                )));
        report.pipeInput("r-team", reportJson);

        String postDeletedJson = mapToJson(java.util.Map.of(
                "eventTime", "2026-05-13T20:02:00Z",
                "reportId", "r-team",
                "postId", contentId,
                "contentId", contentId,
                "status", "post-deleted",
                "postOwnerId", "owner-1",
                "signatureId", "sig-1"
        ));
        deleted.pipeInput(contentId, postDeletedJson);

        String objectionJson = mapToJson(java.util.Map.of(
                "eventTime", "2026-05-13T20:03:00Z",
                "reportId", "r-team",
                "postId", contentId,
                "contentId", contentId,
                "status", "objection-approved",
                "postOwnerId", "owner-1"
        ));
        objection.pipeInput("r-team", objectionJson);

        ContentDecisionState state = store.get(contentId);
        assertThat(state).isNotNull();
        assertThat(state.getLifecycleStatus()).isEqualTo(LifecycleStatus.RESTORED);
        assertThat(state.getDecisionTrace())
                .extracting("eventType")
                .containsExactly(EventType.VERIFICATION, EventType.REPORT,
                        EventType.DELETION, EventType.OBJECTION_APPROVED);
        // Verify the new top-level status strings flow through to the trace verbatim.
        assertThat(state.getDecisionTrace().get(2).status()).isEqualTo("post-deleted");
        assertThat(state.getDecisionTrace().get(3).status()).isEqualTo("objection-approved");
    }

    @Test
    void replayProducesSameEventIdsAndIsIdempotent() throws Exception {
        // A producer that did NOT include an explicit eventId should still be deduplicable
        // across replays: the normalizer must derive a stable synthetic/hash id.
        String contentId = "post-replay";
        String legacyJson = mapToJson(java.util.Map.of(
                "userId", "user-x",
                "type", "verification-verified",
                "message", "no eventId field",
                "eventTime", "2026-05-12T20:00:00Z",
                "payload", java.util.Map.of(
                        "verificationId", "v-replay",
                        "contentId", contentId,
                        "contentUrl", contentId,
                        "status", "verified"
                        // intentionally no eventId
                )));
        verification.pipeInput("v-replay", legacyJson);
        verification.pipeInput("v-replay", legacyJson);

        ContentDecisionState state = store.get(contentId);
        // Two identical legacy payloads must still produce a single decision after dedupe.
        assertThat(state.getDecisionTrace()).hasSize(1);
    }

    @Test
    void verificationStatusIsCanonicalizedWhenOnlyTypeIsPresent() throws Exception {
        // Producer variant: no nested payload, only top-level `type` carries the lifecycle hint.
        // The normalizer must canonicalize "verification-verified" -> "verified" so that
        // the state machine's `equals("verified")` branch fires and lifecycle becomes VERIFIED.
        String contentId = "content-canon-1";
        String json = mapToJson(java.util.Map.of(
                "eventTime", "2026-05-12T20:00:00Z",
                "eventId", "ev-canon-1",
                "userId", "u-1",
                "contentId", contentId,
                "type", "verification-verified"
        ));
        verification.pipeInput("v-canon-1", json);

        ContentDecisionState state = store.get(contentId);
        assertThat(state).isNotNull();
        assertThat(state.getLifecycleStatus()).isEqualTo(LifecycleStatus.VERIFIED);
        assertThat(state.getLastVerificationStatus()).isEqualTo("verified");
        assertThat(state.getDecisionTrace().get(0).status()).isEqualTo("verified");
    }

    @Test
    void verificationStatusFromTopLevelStatusFieldIsAccepted() throws Exception {
        // Producer variant: payload-less envelope with `status: "verified"` at the top level
        // (no `type`). The normalizer used to ignore root.status entirely; it must now pick it up.
        String contentId = "content-canon-2";
        String json = mapToJson(java.util.Map.of(
                "eventTime", "2026-05-12T20:00:00Z",
                "eventId", "ev-canon-2",
                "userId", "u-1",
                "contentId", contentId,
                "status", "verified"
        ));
        verification.pipeInput("v-canon-2", json);

        ContentDecisionState state = store.get(contentId);
        assertThat(state.getLifecycleStatus()).isEqualTo(LifecycleStatus.VERIFIED);
        assertThat(state.getLastVerificationStatus()).isEqualTo("verified");
    }

    @Test
    void rejectedReasonIsPreservedAfterCanonicalization() throws Exception {
        // For rejection, the canonical token keeps the suffix so the trace remains explainable
        // ("rejected-low-quality"), while the state machine still flips to REJECTED because the
        // token starts with "rejected".
        String contentId = "content-canon-3";
        String json = mapToJson(java.util.Map.of(
                "eventTime", "2026-05-12T20:00:00Z",
                "eventId", "ev-canon-3",
                "userId", "u-1",
                "contentId", contentId,
                "type", "verification-rejected-low-quality"
        ));
        verification.pipeInput("v-canon-3", json);

        ContentDecisionState state = store.get(contentId);
        assertThat(state.getLifecycleStatus()).isEqualTo(LifecycleStatus.REJECTED);
        assertThat(state.getLastVerificationStatus()).isEqualTo("rejected-low-quality");
    }

    private String verificationJson(String contentId, String status, String eventTime,
                                     String verificationId) {
        return verificationJson(contentId, status, eventTime, verificationId, null);
    }

    private String verificationJson(String contentId, String status, String eventTime,
                                     String verificationId, String eventId) {
        return mapToJson(java.util.Map.of(
                "userId", "user-1",
                "type", "verification-" + status,
                "message", "ok",
                "eventTime", eventTime,
                "payload", java.util.Map.of(
                        "verificationId", verificationId,
                        "contentId", contentId,
                        "contentUrl", contentId,
                        "status", status,
                        "signatureId", "sig-x",
                        "eventId", eventId == null ? "ev-" + verificationId : eventId
                )
        ));
    }

    private String reportJson(String contentId, String type, String eventTime, String reportId) {
        return mapToJson(java.util.Map.of(
                "userId", "reporter-1",
                "type", type,
                "message", "report",
                "eventTime", eventTime,
                "payload", java.util.Map.of(
                        "reportId", reportId,
                        "postId", contentId,
                        "contentId", contentId
                )
        ));
    }

    private String postDeletedJson(String contentId, String eventTime, String reportId) {
        return mapToJson(java.util.Map.of(
                "reportId", reportId,
                "postId", contentId,
                "contentId", contentId,
                "postOwnerId", "owner-1",
                "signatureId", "sig-1",
                "eventTime", eventTime
        ));
    }

    private String objectionJson(String contentId, String eventTime, String reportId) {
        return mapToJson(java.util.Map.of(
                "reportId", reportId,
                "postId", contentId,
                "contentId", contentId,
                "postOwnerId", "owner-1",
                "eventTime", eventTime
        ));
    }

    private String mapToJson(java.util.Map<String, Object> map) {
        try {
            return mapper.writeValueAsString(map);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    static long parse(String iso) { return Instant.parse(iso).toEpochMilli(); }
}
