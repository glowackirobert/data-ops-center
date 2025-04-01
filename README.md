# Kafka Producer Application

This is a Java-based Kafka Producer application that sends messages to a Kafka topic.


## Requirements

Before you begin, ensure you have the following installed on your machine:

1. Java Development Kit (JDK) 21
2. Apache Maven 3.6
3. Docker/Podman (to run the application in a container)
4. Kafka cluster
5. Schema registry


### Running Zk, Kafka and Schema Registry 

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
podman run --rm -it --network pinot-network --name kafka -p 9092:9092 -p 29092:29092 -e KAFKA_BROKER_ID=0 -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://kafka:9092,PLAINTEXT_HOST://localhost:29092 -e KAFKA_LISTENERS=PLAINTEXT://0.0.0.0:9092,PLAINTEXT_HOST://0.0.0.0:29092 -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP="PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT" -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 bitnami/kafka:3.6
```

Run schema registry:
```bash
podman run --rm -it --network pinot-network --name schema-registry -p 8081:8081 -e SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS=PLAINTEXT://kafka:9092 -e SCHEMA_REGISTRY_HOST_NAME=schema-registry -e SCHEMA_REGISTRY_LISTENERS=http://0.0.0.0:8081 confluentinc/cp-schema-registry:7.6.5
```


### Running the Application in the container

Build the kafka producer app image:
```bash
podman build -t kafka-producer:0.0.1-SNAPSHOT -f containerfile-kafka-producer .
```

Run kafka producer app in the container:
```bash
podman run --rm -it --network pinot-network --name kafka-producer kafka-producer:0.0.1-SNAPSHOT
```

Check kafka producer publish messages:
```bash
podman exec -it kafka /bin/bash -c "/opt/bitnami/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic trade --from-beginning"
```


### Running the Application locally

Build kafka producer app:
```bash
mvn clean package
```

Run application:
```bash
java -jar target/kafka-producer-0.0.1-SNAPSHOT.jar local
```


### Creation of Apache Pinot image with custom configuration

Build the Apache Pinot custom image:
```bash
podman build -t custom-pinot:1.2.0 -f containerfile-apache-pinot .
```

### Running Apache Pinot cluster

Run Apache Pinot cluster:
```bash
podman run --rm -it --network pinot-network --name pinot -p 2123:2123 -p 9000:9000 -p 8000:8000 -p 7050:7050 -p 6000:6000 apachepinot/pinot:1.2.0 QuickStart -type batch
```

Run Apache Pinot controller:
```bash
podman run --rm -it --network pinot-demo --name pinot-controller -p 9000:9000 -e JAVA_OPTS="-Dplugins.dir=/opt/pinot/plugins -Xms1G -Xmx2G -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Xlog:gc:gc-pinot-controller.log" apachepinot/pinot:1.2.0 StartController -zkAddress zookeeper:2181
```

Run Apache Pinot broker:
```bash
podman run --rm -it --network pinot-demo --name pinot-broker -p 8000:8000 -e JAVA_OPTS="-Dplugins.dir=/opt/pinot/plugins -Xms2G -Xmx2G -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Xlog:gc:gc-pinot-broker.log" apachepinot/pinot:1.2.0 StartBroker -zkAddress zookeeper:2181
```

Run Apache Pinot server:
```bash
podman run --rm -it --network pinot-demo --name pinot-server -p 7000:7000 -e JAVA_OPTS="-Dplugins.dir=/opt/pinot/plugins -Xms4G -Xmx8G -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Xlog:gc:gc-pinot-server.log" apachepinot/pinot:1.2.0 StartServer -zkAddress zookeeper:2181
```

Run Apache Pinot minion:
```bash
podman run --rm -it --network pinot-demo --name pinot-minion -p 6000:6000 -e JAVA_OPTS="-Dplugins.dir=/opt/pinot/plugins -Xms1
```


### Running compose

Set environment variable for current session:
```bash
$env:PINOT_IMAGE = "localhost/custom-pinot:1.2.0"
```

Runs all containers:
```bash
podman compose --file .\container-compose.yml up
```


### Stopping compose

Stops all containers:
```bash
podman compose --file .\container-compose.yml down
```
