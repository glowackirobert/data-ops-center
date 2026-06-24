# Kafka Producer Application

This is a Java-based Kafka Producer application that sends messages to a Kafka topic.



## Requirements

Before you begin, ensure you have the following installed on your machine:

1. Java Development Kit (JDK) 21
2. Apache Maven 3.6
3. Docker/Podman



### Run Kafka and Schema Registry

Create network:
```bash
docker network create -d bridge pinot-network
```

Run kafka in KRaft mode:
```bash
docker run --rm -it --network pinot-network --name kafka -p 9092:9092 -p 29092:29092 \
  -e KAFKA_NODE_ID=1 \
  -e KAFKA_PROCESS_ROLES=controller,broker \
  -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
  -e KAFKA_LISTENERS="CONTROLLER://:9093,INTERNAL://:9092,EXTERNAL://:29092" \
  -e KAFKA_ADVERTISED_LISTENERS="INTERNAL://kafka:9092,EXTERNAL://localhost:29092" \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP="CONTROLLER:PLAINTEXT,INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT" \
  -e KAFKA_INTER_BROKER_LISTENER_NAME=INTERNAL \
  -e KAFKA_CONTROLLER_QUORUM_VOTERS="1@kafka:9093" \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
  apache/kafka:4.1.1
```

Run schema registry:
```bash
docker run --rm -it --network pinot-network --name schema-registry -p 8081:8081 -e SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS=PLAINTEXT://kafka:9092 -e SCHEMA_REGISTRY_HOST_NAME=schema-registry -e SCHEMA_REGISTRY_LISTENERS=http://0.0.0.0:8081 confluentinc/cp-schema-registry:7.6.5
```

### Run the Application locally

Build kafka producer app:
```bash
mvn clean package -pl kafka-producer-app
```

Run application:
```bash
java -jar kafka-producer-app/target/kafka-producer-app-1.0.0.jar local
```

### Run the Application in the container

Build the kafka producer app image:
```bash
docker build -f kafka-producer-app/Dockerfile.kafka-producer-app -t kafka-producer-app:1.0.0 .
```

Run kafka producer app in the container:
```bash
docker run --rm -it --network pinot-network --name kafka-producer-app kafka-producer-app:1.0.0
```

Check if topic exists:
```bash
MSYS_NO_PATHCONV=1 docker exec -it kafka /bin/bash -c "env -u KAFKA_OPTS /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list"
```

Read messages published on kafka topic:
```bash
MSYS_NO_PATHCONV=1 docker exec -it kafka /bin/bash -c "env -u KAFKA_OPTS /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic trade --from-beginning"
```

Check kafka number of messages:
```bash
MSYS_NO_PATHCONV=1 docker exec -it kafka /bin/bash -c "env -u KAFKA_OPTS /opt/kafka/bin/kafka-run-class.sh org.apache.kafka.tools.GetOffsetShell --bootstrap-server localhost:9092 --topic trade --time -1"
```
