# Build stage
FROM maven:3.9.4-eclipse-temurin-21-alpine AS build

WORKDIR /app

COPY pom.xml .

# Download dependencies and cached locally for offline usage
RUN mvn dependency:go-offline -B

# Copy source code and build
COPY src ./src
RUN mvn clean package -U

# Runtime stage
FROM eclipse-temurin:21-jre-alpine

WORKDIR /app

# Copy the JAR file and its dependencies
COPY --from=build /app/target/kafka-producer-*.jar ./
COPY --from=build /app/target/lib/ /app/lib/

ENV CONFIG_TYPE=container

# Run the application using the manifest-defined main class
CMD ["sh", "-c", "java -jar kafka-producer-*.jar ${CONFIG_TYPE}"]