# Kafka Producer Application

This is a Java-based Kafka Producer application that sends messages to a Kafka topic.


## Requirements

Before you begin, ensure you have the following installed on your machine:

1. Java Development Kit (JDK) 21
2. Apache Maven 3.6
3. Docker/Podman (to run the application in a container)
4. Kafka cluster (local or remote)


### Running Kafka

Create network if not exists:
```bash
podman network create -d bridge --ignore pinot-network
```

Run zookeeper:
```bash
podman run --rm -it --network pinot-network --name zookeeper -e ZOOKEEPER_CLIENT_PORT=2181 zookeeper:3.9.2
```

Run kafka:
```bash
podman run --rm -it --network pinot-network --name kafka -p 9092:9092 -p 29092:29092 -e KAFKA_BROKER_ID=0 -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 -e KAFKA_LISTENERS=INTERNAL://0.0.0.0:9092,EXTERNAL://0.0.0.0:29092 -e KAFKA_ADVERTISED_LISTENERS=INTERNAL://kafka:9092,EXTERNAL://localhost:29092 bitnami/kafka:3.6
```


### Running the Application in the container

Build the kafka producer app image:
```bash
podman build -t kafka-producer:0.0.1-SNAPSHOT -f Containerfile-kafka-producer .
```

Run kafka producer app in the container:
```bash
podman run --rm -it --network pinot-network --name kafka-producer kafka-producer:0.0.1-SNAPSHOT
```

Check kafka producer publish messages:
```bash
podman exec -it kafka /bin/bash -c "/opt/bitnami/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic topic --from-beginning"
```


### Running the Application locally

Build kafka producer app:
```bash
mvn clean package
```

Start application:
```bash
java -jar target/kafka-producer-0.0.1-SNAPSHOT.jar local
```





docker build -t custom-apachepinot/pinot:1.3.0 -f Containerfile-apache-pinot .
