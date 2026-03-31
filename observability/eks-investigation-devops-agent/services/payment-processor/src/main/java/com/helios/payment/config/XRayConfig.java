package com.helios.payment.config;

import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

/**
 * AWS X-Ray Configuration for Payment Processor Service
 * Requirements: 10.4
 * 
 * Note: X-Ray SDK 2.x is incompatible with Jackson 2.18+ (Spring Boot 3.5).
 * Tracing is disabled until AWS X-Ray SDK is updated.
 * The X-Ray daemon sidecar can still collect traces via OpenTelemetry auto-instrumentation.
 */
@Configuration
@Profile("!test")
public class XRayConfig {

    private static final Logger logger = LoggerFactory.getLogger(XRayConfig.class);

    @Value("${aws.xray.service-name:payment-processor}")
    private String serviceName;

    @Value("${spring.profiles.active:dev}")
    private String environment;

    @PostConstruct
    public void init() {
        logger.info("X-Ray SDK tracing disabled (Jackson incompatibility) - service: {}, environment: {}",
                serviceName, environment);
    }

    public String getServiceName() {
        return serviceName + "-" + environment;
    }
}
