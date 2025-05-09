import avro.Trade;
import lombok.extern.slf4j.Slf4j;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.clients.producer.RecordMetadata;

import java.util.Objects;
import java.util.Properties;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static util.PropertiesLoader.loadProperties;

@Slf4j
public class KafkaCustomTopicProducer implements KafkaTopicProducer, AutoCloseable {

    private final KafkaProducer<String, Trade> producer;
    private final ExecutorService executorService;

    private static final String TOPIC = "trade";
    private static final String PROPERTIES_FILE_TEMPLATE = "kafka-producer-%s.properties";
    private static final int NUMBER_OF_MESSAGES = 10_000_000;
    private static final int FLUSH_INTERVAL = 100_000;
    private static final int NUMBER_OF_THREADS = 4;

    public KafkaCustomTopicProducer(String configType) {
        Properties properties = loadProperties(String.format(PROPERTIES_FILE_TEMPLATE, configType));
        this.producer = new KafkaProducer<>(Objects.requireNonNull(properties));
        this.executorService = Executors.newFixedThreadPool(NUMBER_OF_THREADS);
    }

    @Override
    public void produce() {
        log.info("produce()");
        AtomicInteger messageCounter = new AtomicInteger(0);
        for (int i = 0; i < NUMBER_OF_THREADS; i++) {
            executorService.submit(() -> produceMessages(messageCounter));
        }
        shutdownAndAwaitTermination();
    }

    @Override
    public void close() {
        log.info("close()");
        shutdownAndAwaitTermination();
        producer.close();
    }

    private void produceMessages(AtomicInteger messageCounter) {
        log.debug("produceMessages({})", messageCounter);
        int localCounter = 0;
        while (true) {
            var currentMessage = messageCounter.getAndIncrement();
            if (currentMessage >= NUMBER_OF_MESSAGES) break;

            sendSingleMessage(createAvroMessage(currentMessage));

            localCounter++;
            if (localCounter % FLUSH_INTERVAL == 0) {
                producer.flush();
                log.info("Thread {} flushed {} messages", Thread.currentThread().getName(), FLUSH_INTERVAL);
            }
        }
    }

    private void sendSingleMessage(Trade trade) {
        log.debug("sendSingleMessage({})", trade);
        ProducerRecord<String, Trade> record = new ProducerRecord<>(TOPIC, trade);
        producer.send(record, this::handleSendResult);
    }

    private Trade createAvroMessage(int i) {
        return Trade.newBuilder()
                .setEventId(String.valueOf(i))
                .build();
    }

    private void handleSendResult(RecordMetadata metadata, Exception exception) {
        if (exception != null) {
            log.error("Error sending message: {}", exception.getMessage());
        } else {
            log.debug("Message sent successfully: topic={}, partition={}, offset={}",
                    metadata.topic(), metadata.partition(), metadata.offset());
        }
    }

    private void shutdownAndAwaitTermination() {
        executorService.shutdown();
        try {
            if (!executorService.awaitTermination(1, TimeUnit.MINUTES)) {
                executorService.shutdownNow();
                if (!executorService.awaitTermination(1, TimeUnit.MINUTES)) {
                    log.error("ExecutorService did not terminate");
                }
            }
        } catch (InterruptedException ex) {
            log.error("Shutdown interrupted. Initiating forced shutdown.");
            executorService.shutdownNow();
            Thread.currentThread().interrupt();
        }
    }

}