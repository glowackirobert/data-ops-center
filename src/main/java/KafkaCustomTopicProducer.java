import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.InputStream;
import java.util.Properties;

public class KafkaCustomTopicProducer implements KafkaTopicProducer {

    private static final Logger LOG = LoggerFactory.getLogger(KafkaCustomTopicProducer.class);
    private static final String TOPIC = "topic";

    @Override
    public void produce() {

        Properties properties = new Properties();
        String propertiesFile = "kafka-producer.properties";
        try (InputStream inputStream = getClass().getClassLoader().getResourceAsStream(propertiesFile)) {
            if (inputStream == null) {
                LOG.error("Unable to find properties file: {}", propertiesFile);
                return;
            }
            properties.load(inputStream);
        } catch (IOException e) {
            LOG.error("Error reading Kafka producer properties", e);
            return;
        }
        // create the producer
        KafkaProducer<String, String> producer = new KafkaProducer<>(properties);
        LOG.info("Kafka producer created with properties: {}", properties);

        ProducerRecord<String, String> producerRecord = new ProducerRecord<>(TOPIC, "topic");
        LOG.info("Producer record created for topic: {}, value: {}", "topic", "topic");
    }
}
