package com.edpo.contentledger.web;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

import org.apache.kafka.streams.KafkaStreams;
import org.apache.kafka.streams.StoreQueryParameters;
import org.apache.kafka.streams.state.KeyValueIterator;
import org.apache.kafka.streams.state.QueryableStoreTypes;
import org.apache.kafka.streams.state.ReadOnlyKeyValueStore;
import org.springframework.kafka.config.StreamsBuilderFactoryBean;
import org.springframework.stereotype.Service;

import com.edpo.contentledger.config.ContentLedgerProperties;
import com.edpo.contentledger.model.ContentDecisionState;

/**
 * Wraps Kafka Streams Interactive Queries against the {@code content-state-store} KTable.
 * For a single-instance deployment (course scope) we query the local store directly.
 * In a multi-instance setup we'd add a metadata lookup via {@link KafkaStreams#queryMetadataForKey}.
 */
@Service
public class LedgerQueryService {

    private final StreamsBuilderFactoryBean streamsFactory;
    private final ContentLedgerProperties props;

    public LedgerQueryService(StreamsBuilderFactoryBean streamsFactory, ContentLedgerProperties props) {
        this.streamsFactory = streamsFactory;
        this.props = props;
    }

    public Optional<ContentDecisionState> findById(String contentId) {
        return store().map(s -> s.get(contentId));
    }

    public List<String> listContentIds(int limit) {
        return store().map(s -> {
            List<String> keys = new ArrayList<>();
            try (KeyValueIterator<String, ContentDecisionState> it = s.all()) {
                while (it.hasNext() && keys.size() < limit) {
                    keys.add(it.next().key);
                }
            }
            return keys;
        }).orElseGet(List::of);
    }

    public List<ContentDecisionState> listStates(int limit) {
        return store().map(s -> {
            List<ContentDecisionState> all = new ArrayList<>();
            try (KeyValueIterator<String, ContentDecisionState> it = s.all()) {
                while (it.hasNext() && all.size() < limit) {
                    all.add(it.next().value);
                }
            }
            return all;
        }).orElseGet(List::of);
    }

    public StreamHealth streamHealth() {
        KafkaStreams streams = streamsFactory.getKafkaStreams();
        if (streams == null) {
            return new StreamHealth("NOT_INITIALIZED", false);
        }
        KafkaStreams.State state = streams.state();
        return new StreamHealth(state.name(), state.isRunningOrRebalancing());
    }

    private Optional<ReadOnlyKeyValueStore<String, ContentDecisionState>> store() {
        KafkaStreams streams = streamsFactory.getKafkaStreams();
        if (streams == null) return Optional.empty();
        KafkaStreams.State state = streams.state();
        if (!state.isRunningOrRebalancing()) return Optional.empty();
        try {
            return Optional.of(streams.store(StoreQueryParameters.fromNameAndType(
                    props.getStore().getContentState(),
                    QueryableStoreTypes.keyValueStore())));
        } catch (Exception ex) {
            return Optional.empty();
        }
    }

    public record StreamHealth(String state, boolean ready) {}
}
