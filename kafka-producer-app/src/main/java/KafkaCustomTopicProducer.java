import avro.Trade;
import avro.TradeSide;
import avro.TradeType;
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
    private static final int NUMBER_OF_MESSAGES = 10_000;
    private static final int FLUSH_INTERVAL = 100;
    private static final int NUMBER_OF_THREADS = 1;
    private static final int ITERATIONS = 2;
    private final KafkaProducer<String, Trade> producer;

    public KafkaCustomTopicProducer(String configType) {
        Properties properties = loadProperties(String.format(PROPERTIES_FILE_TEMPLATE, configType));
        this.producer = new KafkaProducer<>(Objects.requireNonNull(properties));
    }

    @Override
    public void produce() {
        for (int iteration = 0; iteration < ITERATIONS; iteration++) {
            log.info("Starting iteration {}/{}", iteration + 1, ITERATIONS);
            AtomicInteger messageCounter = new AtomicInteger(0);
            ExecutorService executorService = Executors.newFixedThreadPool(NUMBER_OF_THREADS);

            for (int i = 0; i < NUMBER_OF_THREADS; i++) {
                executorService.submit(() -> produceMessages(messageCounter));
            }
            shutdownExecutor(executorService);
        }
    }

    @Override
    public void close() {
        log.info("Closing producer and executor service");
        producer.flush();
        producer.close();
    }

    private void produceMessages(AtomicInteger messageCounter) {
        int localCounter = 0;
        long lastFlushTime = System.nanoTime();
        while (true) {
            int currentMsgIndex = messageCounter.getAndIncrement();
            if (currentMsgIndex >= NUMBER_OF_MESSAGES) {
                break;
            }
            Trade trade = createAvroMessage(currentMsgIndex);
            if (localCounter == 100) {
                log.info("trade: {}", trade);
            }
            sendSingleMessage(trade);

            localCounter++;
            if (localCounter % FLUSH_INTERVAL == 0) {
                producer.flush();
                long now = System.nanoTime();
                long elapsedNanos = now - lastFlushTime;
                lastFlushTime = now;
                double elapsedSeconds = elapsedNanos / 1_000_000_000.0;
                log.info("Thread: {} flushed {} messages in {} sec.",
                        Thread.currentThread().getName(), FLUSH_INTERVAL, elapsedSeconds);
            }
        }
    }

    private void sendSingleMessage(Trade trade) {
        ProducerRecord<String, Trade> record = new ProducerRecord<>(TOPIC, trade);
        producer.send(record, this::handleSendResult);
    }

    private Trade createAvroMessage(int index) {
        return Trade.newBuilder()
                .setEventId(String.valueOf(index))
                .setSymbol(RandomGenerator.generateStringValue())
                .setTradeDate(RandomGenerator.generateRandomNanoTimestamp())
                .setQuantity(RandomGenerator.generateIntValue())
                .setIsActive(RandomGenerator.generateBooleanValue())
                .setSide(RandomGenerator.getRandomEnumValue(TradeSide.class))
                .setTradeType(RandomGenerator.getRandomEnumValue(TradeType.class))
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

    private void shutdownExecutor(ExecutorService executorService) {
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
