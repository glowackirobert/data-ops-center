import lombok.extern.slf4j.Slf4j;

@Slf4j
public class KafkaProducerApp {

    public static void main(String[] args) {
        log.info("Starting Kafka Producer Application");

        if (args.length < 1) {
            log.error("Please provide configuration type (local or container)");
            System.exit(1);
        }

        String configType = args[0];
        log.info("Starting Kafka Producer with configuration: {}", configType);

        try (KafkaTopicProducer kafkaProducer = new KafkaCustomTopicProducer(configType)) {
            kafkaProducer.produce();
            log.info("Kafka Producer Application finished");
        } catch (Exception e) {
            log.error("Error in Kafka Producer Application: {}", e.getMessage(), e);
            System.exit(1);
        }
    }
}
