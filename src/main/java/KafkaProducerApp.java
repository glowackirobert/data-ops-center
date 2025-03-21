public class KafkaProducerApp {

    public static void main(String[] args) {

        KafkaTopicProducer kafkaProducer = new KafkaCustomTopicProducer();
        kafkaProducer.produce();
    }
}