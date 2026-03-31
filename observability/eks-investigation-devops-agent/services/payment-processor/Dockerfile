# Runtime-only Dockerfile for Payment Processor Service
#
# Build the JAR locally first (runs natively, fast):
#   mvn -f services/payment-processor/pom.xml clean package -DskipTests -B
#
# Then build the container image:
#   finch build --platform linux/arm64 -t <tag> services/payment-processor/

FROM public.ecr.aws/docker/library/eclipse-temurin:21-jre-alpine

# Create non-root user for security
RUN addgroup -g 1001 -S appgroup && \
    adduser -u 1001 -S appuser -G appgroup

WORKDIR /app

# Copy the pre-built JAR
COPY target/*.jar app.jar

# Set ownership to non-root user
RUN chown -R appuser:appgroup /app

USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8080/actuator/health || exit 1

EXPOSE 8080

# JVM options for container environment
ENV JAVA_OPTS="-XX:+UseContainerSupport -XX:MaxRAMPercentage=75.0 -XX:InitialRAMPercentage=50.0"

ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
