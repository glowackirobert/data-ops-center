import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class KafkaProducerApp {

    public static final Logger LOG = LoggerFactory.getLogger(KafkaProducerApp.class);

    public static void main(String[] args) {
        LOG.info("Starting Kafka Producer Application");

        if (args.length < 1) {
            LOG.error("Please provide configuration type (local or container)");
            System.exit(1);
        }

        String configType = args[0];
        LOG.info("Starting Kafka Producer with configuration: {}", configType);

        try (KafkaTopicProducer kafkaProducer = new KafkaCustomTopicProducer(configType)) {
            kafkaProducer.produce();
            LOG.info("Kafka Producer Application finished");
        } catch (Exception e) {
            LOG.error("Error in Kafka Producer Application: {}", e.getMessage(), e);
            System.exit(1);
        }
    }
}