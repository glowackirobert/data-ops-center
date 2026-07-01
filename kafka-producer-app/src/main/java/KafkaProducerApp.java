import lombok.extern.slf4j.Slf4j;

@Slf4j
public class KafkaProducerApp {

    public static void main(String[] args) {
        log.info("Starting Kafka Producer Application");

        try (KafkaTopicProducer kafkaProducer = new KafkaCustomTopicProducer()) {
            kafkaProducer.produce();
            log.info("Kafka Producer Application finished");
        } catch (Exception e) {
            log.error("Error in Kafka Producer Application: {}", e.getMessage(), e);
            System.exit(1);
        }
    }
}
