# Kafka Producer Application

This is a Java-based Kafka Producer application that sends messages to a Kafka topic.


## Requirements

Before you begin, ensure you have the following installed on your machine:

1. Java Development Kit (JDK) 21 or later
2. Apache Maven 3.6 or later
3. Kafka cluster (local or remote)


## Building the Application

To build the application, follow these steps:

1. Clone this repository to your local machine.
2. Navigate to the project root directory.
3. Run the following command to build the project:

```bash
mvn clean package
```

### Running Kafka
To run the application in a Docker/Podman container, follow these steps firstly to ensure Kafka is running
Run zookeeper:
```bash
podman run --rm -it --network pinot-demo --name zookeeper -e ZOOKEEPER_CLIENT_PORT=2181 zookeeper:3.9.2
```

Run kafka:
```bash
podman run --rm -it --network pinot-demo --name kafka -p 9092:9092 -e KAFKA_BROKER_ID=0 -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 bitnami/kafka:3.6
```

### Running the Application locally
Start application:
```bash
java -jar target/kafka-producer-0.0.1-SNAPSHOT.jar local
```