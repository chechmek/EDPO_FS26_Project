# --- Build stage -------------------------------------------------------------
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /workspace

# Cache dependencies separately from source for faster rebuilds.
COPY pom.xml ./
RUN mvn -B -q -e -DskipTests dependency:go-offline || true

COPY src ./src
RUN mvn -B -DskipTests package

# --- Runtime stage -----------------------------------------------------------
FROM eclipse-temurin:21-jre
WORKDIR /app

COPY --from=build /workspace/target/content-ledger-*.jar /app/app.jar

EXPOSE 8085
ENV SERVER_PORT=8085 \
    KAFKA_BOOTSTRAP_SERVERS=kafka:29092 \
    JAVA_OPTS=""

ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar /app/app.jar"]
