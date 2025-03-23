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
    public void produce(String configType) {
        Properties properties = new Properties();
        String propertiesFile = "kafka-producer-" + configType + ".properties";
        LOG.info("Properties file selected: {}", propertiesFile);
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
        try (KafkaProducer<String, String> producer = new KafkaProducer<>(properties)) {
            LOG.info("Kafka producer created with properties: {}", properties);

            // Format the message as JSON
            String message = "topic";
            String jsonMessage = "{\"message\": \"" + message + "\"}";

            ProducerRecord<String, String> producerRecord = new ProducerRecord<>(TOPIC, jsonMessage);
            LOG.info("Producer record created for topic: {}, value: {}", TOPIC, jsonMessage);
    
            // send data
            producer.send(producerRecord, (metadata, exception) -> {
                if (exception == null) {
                    LOG.info("Record sent successfully to topic: {}, partition: {}, offset: {}",
                            metadata.topic(), metadata.partition(), metadata.offset());
                } else {
                    LOG.error("Error while sending record", exception);
                }
            });
    
            producer.flush();
            LOG.info("Producer data flushed");
    
            // The producer will be automatically closed when exiting the try-with-resources block
        }
        LOG.info("Producer closed");
    }
}
