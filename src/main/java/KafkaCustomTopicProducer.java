import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.InputStream;
import java.util.Objects;
import java.util.Properties;
import java.util.stream.IntStream;

public class KafkaCustomTopicProducer implements KafkaTopicProducer {

    private static final Logger LOG = LoggerFactory.getLogger(KafkaCustomTopicProducer.class);
    private final KafkaProducer<String, String> producer;
    private static final String TOPIC = "topic";
    private static final String PROPERTIES_FILE_TEMPLATE = "kafka-producer-%s.properties";
    private static final String JSON_MESSAGE_TEMPLATE = "{\"message\": \"%s_%d\"}";
    private static final int numberOfMessages = 100_000;

    public KafkaCustomTopicProducer(String configType) {
        Properties properties = loadProducerProperties(configType);
        this.producer = new KafkaProducer<>(Objects.requireNonNull(properties));
    }

    @Override
    public void produce() {
        IntStream.range(0, numberOfMessages).forEachOrdered(i -> sendSingleMessage(this.producer, createJsonMessage(i)));
    }

    private Properties loadProducerProperties(String configType) {
        String propertiesFile = String.format(PROPERTIES_FILE_TEMPLATE, configType);
        LOG.info("Loading properties from: {}", propertiesFile);

        try (InputStream inputStream = getClass().getClassLoader().getResourceAsStream(propertiesFile)) {
            if (inputStream == null) {
                LOG.error("Properties file not found: {}", propertiesFile);
                return null;
            }

            Properties properties = new Properties();
            properties.load(inputStream);
            LOG.debug("Loaded {} properties", properties.size());
            return properties;

        } catch (IOException e) {
            LOG.error("Failed to load properties from {}", propertiesFile, e);
            return null;
        }
    }

    private void sendSingleMessage(KafkaProducer<String, String> producer, String message) {
        ProducerRecord<String, String> record = new ProducerRecord<>(TOPIC, message);
        LOG.info("Sending message to topic {}: {}", TOPIC, message);

        try {
            producer.send(record, this::handleSendResult).get();
            LOG.info("Message sent and acknowledged successfully");
        } catch (Exception e) {
            LOG.error("Failed to send message to Kafka", e);
            Thread.currentThread().interrupt();
        }
    }

    private String createJsonMessage(int i) {
        return String.format(JSON_MESSAGE_TEMPLATE, TOPIC, i);
    }

    private void handleSendResult(org.apache.kafka.clients.producer.RecordMetadata metadata, Exception exception) {
        if (exception != null) {
            LOG.error("Message failed to send", exception);
        } else {
            LOG.info("Message sent successfully [topic: {}, partition: {}, offset: {}]",
                    metadata.topic(), metadata.partition(), metadata.offset());
        }
    }
}
