package producer;

public interface KafkaTopicProducer extends AutoCloseable {

    void produce();
}