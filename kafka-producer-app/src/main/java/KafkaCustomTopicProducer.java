import avro.Trade;
import lombok.extern.slf4j.Slf4j;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.clients.producer.RecordMetadata;

import java.util.Objects;
import java.util.Properties;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

import static util.PropertiesLoader.loadProperties;

@Slf4j
public class KafkaCustomTopicProducer implements KafkaTopicProducer, AutoCloseable {

    private static final String TOPIC = "trade";
    private static final String PROPERTIES_FILE_TEMPLATE = "kafka-producer-%s.properties";
    private static final int NUMBER_OF_MESSAGES = 10_000_000;
    private static final int FLUSH_INTERVAL = 100_000;
    private static final int NUMBER_OF_THREADS = 4;

    private final KafkaProducer<String, Trade> producer;
    private final ExecutorService executorService;
    private final AtomicInteger messageCounter = new AtomicInteger(0);

    public KafkaCustomTopicProducer(String configType) {
        Properties properties = loadProperties(String.format(PROPERTIES_FILE_TEMPLATE, configType));
        this.producer = new KafkaProducer<>(Objects.requireNonNull(properties));
        this.executorService = Executors.newFixedThreadPool(NUMBER_OF_THREADS);
    }

    @Override
    public void produce() {
        log.info("Starting to produce messages");
        for (int i = 0; i < NUMBER_OF_THREADS; i++) {
            executorService.submit(this::produceMessages);
        }
    }

    @Override
    public void close() {
        log.info("Closing producer and executor service");
        shutdownExecutor();
        producer.close();
    }

    private void produceMessages() {
        int localCounter = 0;
        while (true) {
            int currentMsgIndex = messageCounter.getAndIncrement();
            if (currentMsgIndex >= NUMBER_OF_MESSAGES) {
                break;
            }
            Trade trade = createAvroMessage(currentMsgIndex);
            sendSingleMessage(trade);

            localCounter++;
            if (localCounter % FLUSH_INTERVAL == 0) {
                producer.flush();
                log.info("Thread {} flushed {} messages", Thread.currentThread().getName(), FLUSH_INTERVAL);
            }
        }
    }

    private void sendSingleMessage(Trade trade) {
        ProducerRecord<String, Trade> record = new ProducerRecord<>(TOPIC, trade);
        producer.send(record, this::handleSendResult);
    }

    private Trade createAvroMessage(int index) {
        return Trade.newBuilder()
                .setTradeId(String.valueOf(index))
                .setSymbol(RandomGenerator.generateStringValue())
                .build();
    }

    private void handleSendResult(RecordMetadata metadata, Exception exception) {
        if (exception != null) {
            log.error("Error sending message", exception);
        } else {
            log.debug("Message sent successfully: topic={}, partition={}, offset={}",
                    metadata.topic(), metadata.partition(), metadata.offset());
        }
    }

    private void shutdownExecutor() {
        executorService.shutdown();
        try {
            if (!executorService.awaitTermination(1, TimeUnit.MINUTES)) {
                log.warn("Executor did not terminate in the specified time, forcing shutdown");
                executorService.shutdownNow();
                if (!executorService.awaitTermination(1, TimeUnit.MINUTES)) {
                    log.error("Executor failed to terminate");
                }
            }
        } catch (InterruptedException e) {
            log.error("Interrupted while shutting down executor, forcing shutdown", e);
            executorService.shutdownNow();
            Thread.currentThread().interrupt();
        }
    }
}
