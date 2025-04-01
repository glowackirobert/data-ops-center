import avro.Trade;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.clients.producer.RecordMetadata;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Objects;
import java.util.Properties;
import java.util.stream.IntStream;

import static util.PropertiesLoader.loadProperties;

public class KafkaCustomTopicProducer implements KafkaTopicProducer, AutoCloseable {

    private static final Logger LOG = LoggerFactory.getLogger(KafkaCustomTopicProducer.class);
    private static final String TOPIC = "trade";
    private static final String PROPERTIES_FILE_TEMPLATE = "kafka-producer-%s.properties";
    private static final int NUMBER_OF_MESSAGES = 10_000_000;
    private static final int FLUSH_INTERVAL = 10_000;

    private final KafkaProducer<String, Trade> producer;

    public KafkaCustomTopicProducer(String configType) {
        Properties properties = loadProperties(String.format(PROPERTIES_FILE_TEMPLATE, configType));
        this.producer = new KafkaProducer<>(Objects.requireNonNull(properties));
    }

    @Override
    public void produce() {
        IntStream.range(0, NUMBER_OF_MESSAGES).forEach(this::sendAndFlushMessage);
    }

    @Override
    public void close() {
        if (producer != null) {
            producer.flush();
            producer.close();
        }
    }

    private void sendAndFlushMessage(int messageNumber) {
        Trade trade = createAvroMessage(messageNumber);
        sendSingleMessage(trade, messageNumber);
        flushIfNeeded(messageNumber);
    }

    private Trade createAvroMessage(int i) {
        return Trade.newBuilder()
                .setEventId(String.valueOf(i))
                .build();
    }

    private void sendSingleMessage(Trade trade, int messageNumber) {
        ProducerRecord<String, Trade> record = new ProducerRecord<>(TOPIC, trade);
        try {
            producer.send(record, this::handleSendResult);
        } catch (Exception e) {
            LOG.error("Error in sendSingleMessage for message {}", messageNumber, e);
        }
    }

    private void handleSendResult(RecordMetadata metadata, Exception exception) {
        if (exception == null) {
            LOG.info("Message sent successfully: topic={}, partition={}, offset={}",
                    metadata.topic(), metadata.partition(), metadata.offset());
        } else {
            LOG.error("Error sending message: {}", exception.getMessage());
        }
    }

    private void flushIfNeeded(int messageNumber) {
        if ((messageNumber + 1) % FLUSH_INTERVAL == 0) {
            producer.flush();
            LOG.info("Flushed {} messages", FLUSH_INTERVAL);
        }
    }

}