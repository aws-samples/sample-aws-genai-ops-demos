package com.helios.payment.config;

import jakarta.servlet.Filter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.FilterConfig;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Profile;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

import java.io.IOException;

/**
 * X-Ray Servlet Filter - passthrough stub.
 * 
 * X-Ray SDK 2.x is incompatible with Jackson 2.18+ (Spring Boot 3.5).
 * This filter is a no-op until the SDK is updated.
 */
@Component
@Order(1)
@Profile("!test")
public class XRayFilter implements Filter {

    private static final Logger logger = LoggerFactory.getLogger(XRayFilter.class);

    @Override
    public void init(FilterConfig filterConfig) throws ServletException {
        logger.info("X-Ray filter initialized (passthrough mode - SDK disabled)");
    }

    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
            throws IOException, ServletException {
        chain.doFilter(request, response);
    }

    @Override
    public void destroy() {
        // no-op
    }
}
