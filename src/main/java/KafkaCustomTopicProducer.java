import avro.Trade;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Objects;
import java.util.stream.IntStream;

import static util.PropertiesLoader.loadProperties;

public class KafkaCustomTopicProducer implements KafkaTopicProducer {

    private static final Logger LOG = LoggerFactory.getLogger(KafkaCustomTopicProducer.class);
    private final KafkaProducer<String, Trade> producer;
    private static final String TOPIC = "topic";
    private static final String PROPERTIES_FILE_TEMPLATE = "kafka-producer-%s.properties";
    private static final String JSON_MESSAGE_TEMPLATE = "{\"message\": \"%s_%d\"}";
    private static final int numberOfMessages = 100_000;

    public KafkaCustomTopicProducer(String configType) {
        var propertiesFile = String.format(PROPERTIES_FILE_TEMPLATE, configType);
        var properties = loadProperties(propertiesFile);
        this.producer = new KafkaProducer<>(Objects.requireNonNull(properties));
    }

    @Override
    public void produce() {
        IntStream.range(0, numberOfMessages).forEachOrdered(i -> sendSingleMessage(this.producer, createAvroMessage(i)));
    }

    private void sendSingleMessage(KafkaProducer<String, Trade> producer, Trade trade) {
        ProducerRecord<String, Trade> record = new ProducerRecord<>(TOPIC, trade);
        LOG.info("Sending message to topic {}: {}", TOPIC, trade);

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

//    private Trade createAvroMessage(int i) {
//        return Trade.newBuilder()
//                .setId("1")
//                .setUUID("A")
//                .setTrade("B")
//                .setCurrency("B")
//                .setAmount("12")
//                .setT1("T1")
//                .setT2("T2")
//                .setT3("T3")
//                .setT4("T4")
//                .setT5("T5")
//                .setT6("T6")
//                .setT7("T7")
//                .setBValue(true)
//                .setCreatedAt(Instant.ofEpochSecond(System.currentTimeMillis()))
//                .build();
//    }

    private Trade createAvroMessage(int i) {
        return Trade.newBuilder()
                .setEventId(String.valueOf(i))
                .setEventTime(System.currentTimeMillis())
                .build();
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
