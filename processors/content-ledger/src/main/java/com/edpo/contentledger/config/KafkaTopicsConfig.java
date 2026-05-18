package com.edpo.contentledger.config;

import java.util.Map;

import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.common.config.TopicConfig;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;

/**
 * Declares all topics used by the topology.
 * Spring Kafka's {@code KafkaAdmin} creates topics idempotently on startup.
 * Input topics are also declared so Kafka Streams can start even if a producer
 * has not emitted to a topic yet (for example objection-approved in the demo flow).
 */
@Configuration
@EnableConfigurationProperties(ContentLedgerProperties.class)
public class KafkaTopicsConfig {

    @Bean
    public NewTopic verificationNotificationTopic(ContentLedgerProperties props) {
        return TopicBuilder.name(props.getTopics().getVerificationNotification())
                .partitions(1)
                .replicas(1)
                .build();
    }

    @Bean
    public NewTopic reportNotificationTopic(ContentLedgerProperties props) {
        return TopicBuilder.name(props.getTopics().getReportNotification())
                .partitions(1)
                .replicas(1)
                .build();
    }

    @Bean
    public NewTopic postDeletedTopic(ContentLedgerProperties props) {
        return TopicBuilder.name(props.getTopics().getPostDeleted())
                .partitions(1)
                .replicas(1)
                .build();
    }

    @Bean
    public NewTopic objectionApprovedTopic(ContentLedgerProperties props) {
        return TopicBuilder.name(props.getTopics().getObjectionApproved())
                .partitions(1)
                .replicas(1)
                .build();
    }

    @Bean
    public NewTopic contentDecisionLedgerTopic(ContentLedgerProperties props) {
        return TopicBuilder.name(props.getTopics().getOutput())
                .partitions(1)
                .replicas(1)
                .configs(Map.of(
                        TopicConfig.CLEANUP_POLICY_CONFIG, TopicConfig.CLEANUP_POLICY_COMPACT,
                        TopicConfig.MIN_CLEANABLE_DIRTY_RATIO_CONFIG, "0.1",
                        TopicConfig.SEGMENT_MS_CONFIG, String.valueOf(60_000L)
                ))
                .build();
    }
}
