package com.edpo.contentledger.serde;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Map;

import org.apache.kafka.common.errors.SerializationException;
import org.apache.kafka.common.serialization.Deserializer;
import org.apache.kafka.common.serialization.Serde;
import org.apache.kafka.common.serialization.Serializer;

import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * Registry-less JSON Serde. We chose JSON over Avro for this iteration because:
 *  - Python producers in the existing services already emit JSON.
 *  - No Schema Registry container yet.
 * Avro schemas are still provided under {@code schemas/content-ledger/} as documentation
 * and to keep the door open for a registry-based migration.
 */
public class JsonSerde<T> implements Serde<T> {

    private final Class<T> type;
    private final ObjectMapper mapper;

    public JsonSerde(Class<T> type, ObjectMapper mapper) {
        this.type = type;
        this.mapper = mapper;
    }

    @Override
    public Serializer<T> serializer() {
        return new Serializer<>() {
            @Override
            public byte[] serialize(String topic, T data) {
                if (data == null) return null;
                try {
                    return mapper.writeValueAsBytes(data);
                } catch (Exception e) {
                    throw new SerializationException("Failed to serialize " + type.getSimpleName(), e);
                }
            }
        };
    }

    @Override
    public Deserializer<T> deserializer() {
        return new Deserializer<>() {
            @Override
            public T deserialize(String topic, byte[] data) {
                if (data == null) return null;
                try {
                    return mapper.readValue(data, type);
                } catch (IOException e) {
                    throw new SerializationException(
                            "Failed to deserialize " + type.getSimpleName() + " from topic " + topic
                                    + " value=" + new String(data, StandardCharsets.UTF_8), e);
                }
            }
        };
    }

    @Override
    public void configure(Map<String, ?> configs, boolean isKey) { }

    @Override
    public void close() { }
}
