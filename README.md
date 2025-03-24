# Kafka Producer Application

This is a Java-based Kafka Producer application that sends messages to a Kafka topic.


## Requirements

Before you begin, ensure you have the following installed on your machine:

1. Java Development Kit (JDK) 21
2. Apache Maven 3.6
3. Docker (if you want to run the application in a container)
4. Kafka cluster (local or remote)


### Running Kafka

To run the application in a container, follow these steps firstly to ensure Kafka is running
Create network:
```bash
podman network create -d bridge pinot-network
```

Run zookeeper:
```bash
podman run --rm -it --network pinot-network --name zookeeper -e ZOOKEEPER_CLIENT_PORT=2181 zookeeper:3.9.2
```

Run kafka /when kafka-producer app is running locally/:
```bash
podman run --rm -it --network pinot-network --name kafka -p 9092:9092 -e KAFKA_BROKER_ID=0 -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 bitnami/kafka:3.6
```

Run kafka /when kafka-producer app is running in the container/:
```bash
podman run --rm -it --network pinot-network --name kafka -p 9092:9092 -e KAFKA_BROKER_ID=0 -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://kafka:9092 -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 bitnami/kafka:3.6
```


### Running the Application in a container
To run the application in a container, follow these steps

Build the image of kafka producer app:
```bash
podman build -t kafka-producer:0.0.1-SNAPSHOT .
```

Run kafka producer app in the container:
```bash
podman run --rm -it --network pinot-network --name kafka-producer kafka-producer:0.0.1-SNAPSHOT
```

Ensure kafka producer publish messages properly:
```bash
podman exec -it kafka /bin/bash -c "/opt/bitnami/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic topic --from-beginning"
```


### Running the Application locally

Run the following command to build the project:

```bash
mvn clean package
```

Start application:
```bash
java -jar target/kafka-producer-0.0.1-SNAPSHOT.jar local
```