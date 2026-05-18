package com.edpo.contentledger.stream;

import java.time.Instant;
import java.util.function.Function;

import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.streams.KeyValue;
import org.apache.kafka.streams.StreamsBuilder;
import org.apache.kafka.streams.kstream.Consumed;
import org.apache.kafka.streams.kstream.Grouped;
import org.apache.kafka.streams.kstream.KStream;
import org.apache.kafka.streams.kstream.Materialized;
import org.apache.kafka.streams.kstream.Produced;
import org.apache.kafka.streams.state.KeyValueBytesStoreSupplier;
import org.apache.kafka.streams.state.Stores;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import com.edpo.contentledger.config.ContentLedgerProperties;
import com.edpo.contentledger.model.ContentDecisionState;
import com.edpo.contentledger.model.ContentEvent;
import com.edpo.contentledger.serde.JsonSerde;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * Kafka Streams topology for the Content Decision Ledger.
 *
 * <pre>
 *   verification-notification ─┐
 *   report-notification ───────┤  mapValues(normalize) + selectKey(contentId)
 *   post-deleted ──────────────┤
 *   objection-approved ────────┘
 *                              │
 *                              ▼
 *                            merge
 *                              │ key = contentId
 *                              ▼
 *                          groupByKey ──► aggregate ──► KTable("content-state-store")
 *                                                          │
 *                                                          ├─► Interactive Queries (REST)
 *                                                          └─► toStream() ─► content-decision-ledger (log-compacted)
 * </pre>
 *
 * Lecture concepts realized here:
 *  - Week 8: stateless map/filter (normalize), selectKey-triggered repartitioning, merge,
 *    log-compacted output enabling the reprocessing pattern.
 *  - Week 9: KStream→KTable aggregation, Materialized state store (RocksDB), Interactive Queries.
 */
@Configuration
public class ContentLedgerTopology {

    private static final Logger log = LoggerFactory.getLogger(ContentLedgerTopology.class);

    private final ContentLedgerProperties props;
    private final ObjectMapper objectMapper;

    public ContentLedgerTopology(ContentLedgerProperties props, ObjectMapper objectMapper) {
        this.props = props;
        this.objectMapper = objectMapper;
    }

    @Bean
    public JsonSerde<ContentEvent> contentEventSerde() {
        return new JsonSerde<>(ContentEvent.class, objectMapper);
    }

    @Bean
    public JsonSerde<ContentDecisionState> contentStateSerde() {
        return new JsonSerde<>(ContentDecisionState.class, objectMapper);
    }

    @Bean
    public org.apache.kafka.streams.Topology kafkaStreamsTopology(
            StreamsBuilder builder,
            JsonSerde<ContentEvent> eventSerde,
            JsonSerde<ContentDecisionState> stateSerde) {

        ContentLedgerProperties.Topics topics = props.getTopics();
        String storeName = props.getStore().getContentState();

        KStream<String, ContentEvent> verifications = readAndNormalize(
                builder, topics.getVerificationNotification(), EventNormalizer::fromVerification);
        KStream<String, ContentEvent> reports = readAndNormalize(
                builder, topics.getReportNotification(), EventNormalizer::fromReport);
        KStream<String, ContentEvent> deletions = readAndNormalize(
                builder, topics.getPostDeleted(), EventNormalizer::fromPostDeleted);
        KStream<String, ContentEvent> objections = readAndNormalize(
                builder, topics.getObjectionApproved(), EventNormalizer::fromObjectionApproved);

        KStream<String, ContentEvent> merged = verifications
                .merge(reports)
                .merge(deletions)
                .merge(objections)
                .peek((k, v) -> log.debug("merged contentId={} type={} status={}",
                        k, v == null ? null : v.eventType(), v == null ? null : v.status()));

        KeyValueBytesStoreSupplier supplier = Stores.persistentKeyValueStore(storeName);

        var ledgerTable = merged
                .groupByKey(Grouped.with(Serdes.String(), eventSerde))
                .aggregate(
                        ContentDecisionState::new,
                        (contentId, event, state) -> {
                            state.apply(event, Instant.now());
                            if (state.getContentId() == null) {
                                state.setContentId(contentId);
                            }
                            return state;
                        },
                        Materialized.<String, ContentDecisionState>as(supplier)
                                .withKeySerde(Serdes.String())
                                .withValueSerde(stateSerde)
                );

        ledgerTable.toStream()
                .to(topics.getOutput(), Produced.with(Serdes.String(), stateSerde));

        return builder.build();
    }

    private KStream<String, ContentEvent> readAndNormalize(
            StreamsBuilder builder, String topic, Function<JsonNode, ContentEvent> normalizer) {
        return builder
                .stream(topic, Consumed.with(Serdes.String(), Serdes.String()))
                .mapValues((readOnlyKey, raw) -> {
                    if (raw == null) return null;
                    try {
                        JsonNode node = objectMapper.readTree(raw);
                        return normalizer.apply(node);
                    } catch (Exception e) {
                        log.warn("Failed to parse JSON from topic {}: {}", topic, e.getMessage());
                        return null;
                    }
                })
                .filter((k, v) -> v != null)
                .map((readOnlyKey, event) -> new KeyValue<>(event.contentId(), event));
    }
}
