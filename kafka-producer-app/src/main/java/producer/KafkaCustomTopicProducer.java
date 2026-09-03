package producer;

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
import java.util.concurrent.atomic.AtomicLong;

import static util.PropertiesLoader.loadProperties;

@Slf4j
public class KafkaCustomTopicProducer implements KafkaTopicProducer, AutoCloseable {

    private static final String TOPIC = "trade";
    private static final String PROPERTIES_FILE = "kafka-producer.properties";

    /**
     * Env var setting the total messages per iteration, across all threads.
     * <p>
     * Deliberately an env var rather than an entry in the properties file: that
     * file is handed wholesale to the KafkaProducer constructor, which warns
     * about every key it does not recognise, and it is baked into the JAR so it
     * could not vary per environment anyway. Set from
     * {@code cluster-setup/env/env.dev} and {@code env.prod} via
     * container-compose.yml.
     */
    private static final String MESSAGE_COUNT_ENV = "TRADE_MESSAGE_COUNT";

    /**
     * Fallback when the env var is unset or unparseable.
     * <p>
     * Row count is not what this pipeline is for. Nothing queries {@code trade},
     * and the Gdansk tables carry two orders of magnitude more rows. What it
     * uniquely exercises is Avro with Schema Registry - no other topic in the
     * stack does, and it feeds the only Pinot table decoded by
     * {@code KafkaConfluentSchemaRegistryAvroMessageDecoder}.
     * <p>
     * So the count is sized to be large enough to measure producer throughput
     * against (see the flush note in the send loop below: ~69k msg/sec, against
     * ~4k with a periodic flush) and small enough to be disposable. The disk
     * cost is measured and still holds - ~31.4 bytes per message in Kafka's log
     * dir and ~34.2 bytes per row in Pinot, so ~66 bytes across the two, both
     * landing on the same volume under cluster-setup/volumes/ - which puts 5M
     * at ~330 MB. Weigh that figure before raising the count again: 300M would
     * be ~20 GB.
     * <p>
     * {@code env.dev} and {@code env.prod} both set TRADE_MESSAGE_COUNT to this
     * same value. A run this size is trivial on a laptop and on EC2 alike, so
     * unlike the JVM heap variables there is no dev/prod split to make here.
     */
    private static final long DEFAULT_NUMBER_OF_MESSAGES = 5_000_000L;

    private static final int NUMBER_OF_THREADS = 2;
    private static final int ITERATIONS = 1;
    /** How often the main thread reports progress while the workers run. */
    private static final int PROGRESS_INTERVAL_SECONDS = 30;

    private final KafkaProducer<String, Trade> producer;
    private final AtomicLong sendErrors = new AtomicLong();
    private final long numberOfMessages;

    public KafkaCustomTopicProducer() {
        Properties properties = loadProperties(PROPERTIES_FILE);
        this.producer = new KafkaProducer<>(Objects.requireNonNull(properties));
        this.numberOfMessages = resolveMessageCount();
    }

    @Override
    public void produce() {
        for (int iteration = 0; iteration < ITERATIONS; iteration++) {
            log.info("Starting iteration {}/{}: {} messages across {} threads",
                    iteration + 1, ITERATIONS, numberOfMessages, NUMBER_OF_THREADS);
            AtomicLong messageCounter = new AtomicLong(0);
            ExecutorService executorService = Executors.newFixedThreadPool(NUMBER_OF_THREADS);
            long startNanos = System.nanoTime();

            for (int i = 0; i < NUMBER_OF_THREADS; i++) {
                executorService.submit(() -> produceMessages(messageCounter));
            }
            shutdownExecutor(executorService, messageCounter);

            double elapsedSeconds = (System.nanoTime() - startNanos) / 1_000_000_000.0;
            long produced = produced(messageCounter);
            log.info("Iteration {}/{} produced {} messages in {} sec ({} msg/sec)",
                    iteration + 1, ITERATIONS, produced,
                    String.format("%.1f", elapsedSeconds),
                    String.format("%.0f", produced / elapsedSeconds));
        }
    }

    @Override
    public void close() {
        log.info("Closing producer and executor service");
        producer.flush();
        producer.close();
        long errors = sendErrors.get();
        if (errors > 0) {
            log.error("{} messages failed to send", errors);
        }
    }

    /** Reads the env var, falling back to the dev-sized default. */
    private static long resolveMessageCount() {
        String raw = System.getenv(MESSAGE_COUNT_ENV);
        if (raw == null || raw.isBlank()) {
            log.info("{} unset, using default of {} messages",
                    MESSAGE_COUNT_ENV, DEFAULT_NUMBER_OF_MESSAGES);
            return DEFAULT_NUMBER_OF_MESSAGES;
        }
        try {
            long parsed = Long.parseLong(raw.trim());
            if (parsed <= 0) {
                throw new NumberFormatException("not a positive count");
            }
            log.info("{}={} messages", MESSAGE_COUNT_ENV, parsed);
            return parsed;
        } catch (NumberFormatException e) {
            log.warn("Ignoring invalid {}='{}' ({}), using default of {} messages",
                    MESSAGE_COUNT_ENV, raw, e.getMessage(), DEFAULT_NUMBER_OF_MESSAGES);
            return DEFAULT_NUMBER_OF_MESSAGES;
        }
    }

    private void produceMessages(AtomicLong messageCounter) {
        // The interrupt check is what makes shutdownNow() able to stop this loop:
        // without it the workers ran on until the Kafka client happened to throw
        // InterruptException out of send(), discarding whatever was in flight.
        while (!Thread.currentThread().isInterrupted()) {
            long currentMsgIndex = messageCounter.getAndIncrement();
            if (currentMsgIndex >= numberOfMessages) {
                break;
            }
            Trade trade = createAvroMessage(currentMsgIndex);
            if (currentMsgIndex == 0) {
                log.info("Sample trade message: {}", trade);
            }
            sendSingleMessage(trade);
            // Deliberately no periodic producer.flush() here. flush() is a full
            // pipeline barrier - it blocks until every buffered record is acked,
            // and since both threads share one producer, each thread's flush also
            // waits on the other's in-flight batches. Doing it every 10k messages
            // measured ~4k msg/sec against ~69k for the same producer settings
            // without it. Backpressure already comes from buffer.memory (send()
            // blocks once it fills), delivery failures from handleSendResult, and
            // the one flush that actually matters from close().
        }
    }

    private void sendSingleMessage(Trade trade) {
        ProducerRecord<String, Trade> producerRecord = new ProducerRecord<>(TOPIC, trade);
        producer.send(producerRecord, this::handleSendResult);
    }

    private Trade createAvroMessage(long index) {
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
        if (exception == null) {
            return;
        }
        // One line per failure would itself be a disk-space problem at this
        // message count, so only the first carries a stack trace; the rest are
        // counted and reported once, by close().
        if (sendErrors.getAndIncrement() == 0) {
            log.error("Error sending message (further failures counted, not logged)", exception);
        }
    }

    /**
     * Waits for the workers to drain the counter, reporting progress meanwhile.
     * <p>
     * Deliberately has no deadline. The previous awaitTermination(1, MINUTES)
     * was a hard cutoff on a job sized by message count, so any count the two
     * threads could not reach within 60s was silently truncated - which is how a
     * nominal 10-billion-message run ended up putting ~240k rows in Pinot, a
     * different number on every host. An interrupt of this thread still aborts
     * the run promptly, now that the workers watch their own interrupt flag.
     */
    private void shutdownExecutor(ExecutorService executorService, AtomicLong messageCounter) {
        executorService.shutdown();
        try {
            while (!executorService.awaitTermination(PROGRESS_INTERVAL_SECONDS, TimeUnit.SECONDS)) {
                log.info("Producing: {}/{} messages", produced(messageCounter), numberOfMessages);
            }
        } catch (InterruptedException e) {
            log.warn("Interrupted while producing, stopping workers");
            executorService.shutdownNow();
            Thread.currentThread().interrupt();
        }
    }

    /** The counter overshoots by up to one claim per thread; clamp for reporting. */
    private long produced(AtomicLong messageCounter) {
        return Math.min(messageCounter.get(), numberOfMessages);
    }
}
