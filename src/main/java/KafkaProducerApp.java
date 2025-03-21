import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class KafkaProducerApp {

    public static final Logger LOG = LoggerFactory.getLogger(KafkaProducerApp.class);

    public static void main(String[] args) {
        LOG.info("Starting Kafka Producer Application");

        String configType = "local"; // default to local
        if (args.length > 0) {
            configType = args[0];
        }

        KafkaTopicProducer kafkaProducer = new KafkaCustomTopicProducer();
        kafkaProducer.produce(configType);
        LOG.info("Kafka Producer Application finished");
    }
}