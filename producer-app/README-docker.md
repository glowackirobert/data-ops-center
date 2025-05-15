# Kafka Producer Application

This is a Java-based Kafka Producer application that sends messages to a Kafka topic.



## Requirements

Before you begin, ensure you have the following installed on your machine:

1. Java Development Kit (JDK) 21
2. Apache Maven 3.6
3. Docker/Podman



### Run Zk, Kafka and Schema Registry 

Create network:
```bash
docker network create -d bridge pinot-network
```

Run zookeeper:
```bash
docker run --rm -it --network pinot-network --name zookeeper -e ZOOKEEPER_CLIENT_PORT=2181 zookeeper:3.9.2
```

Run kafka:
```bash
docker run --rm -it --network pinot-network --name kafka -p 9092:9092 -p 29092:29092 -e KAFKA_BROKER_ID=0 -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://kafka:9092,PLAINTEXT_HOST://localhost:29092 -e KAFKA_LISTENERS=PLAINTEXT://0.0.0.0:9092,PLAINTEXT_HOST://0.0.0.0:29092 -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP="PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT" -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 bitnami/kafka:3.6
```

Run schema registry:
```bash
docker run --rm -it --network pinot-network --name schema-registry -p 8081:8081 -e SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS=PLAINTEXT://kafka:9092 -e SCHEMA_REGISTRY_HOST_NAME=schema-registry -e SCHEMA_REGISTRY_LISTENERS=http://0.0.0.0:8081 confluentinc/cp-schema-registry:7.6.5
```



### Run the Application locally

Build kafka producer app:
```bash
mvn clean package
```

Run application:
```bash
java -jar target/kafka-producer-1.0.0.jar local
```



### Run the Application in the container

Build the kafka producer app image:
```bash
docker build -f Dockerfile.kafka-producer -t robertglowacki83/kafka-producer:1.0.0 .
```

Run kafka producer app in the container:
```bash
docker run --rm -it --network pinot-network --name kafka-producer robertglowacki83/kafka-producer:1.0.0
```

Check kafka producer publish messages:
```bash
docker exec -it kafka /bin/bash -c "env -u KAFKA_OPTS /opt/bitnami/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic trade --from-beginning"
```