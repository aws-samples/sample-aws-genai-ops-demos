package com.helios.payment.logging;

import jakarta.servlet.*;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;

import java.io.IOException;

/**
 * Logging Filter for Payment Processor
 * Requirements: 10.5
 * 
 * Adds request context to MDC (Mapped Diagnostic Context) for structured logging.
 * Logs HTTP requests with timing and status information.
 */
@Component
public class LoggingFilter implements Filter {

    private static final Logger logger = LoggerFactory.getLogger(LoggingFilter.class);
    
    private static final String CORRELATION_ID_HEADER = "X-Correlation-ID";
    private static final String MERCHANT_ID_HEADER = "X-Merchant-ID";

    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
            throws IOException, ServletException {
        
        HttpServletRequest httpRequest = (HttpServletRequest) request;
        HttpServletResponse httpResponse = (HttpServletResponse) response;
        
        long startTime = System.currentTimeMillis();
        
        try {
            // Extract context from headers
            String correlationId = httpRequest.getHeader(CORRELATION_ID_HEADER);
            String merchantId = httpRequest.getHeader(MERCHANT_ID_HEADER);
            
            // Add to MDC for structured logging
            if (correlationId != null) {
                MDC.put("correlationId", correlationId);
            }
            if (merchantId != null) {
                MDC.put("merchantId", merchantId);
            }
            
            MDC.put("operation", "http_request");
            MDC.put("httpMethod", httpRequest.getMethod());
            MDC.put("endpoint", httpRequest.getRequestURI());
            
            // Process request
            chain.doFilter(request, response);
            
            // Log request completion
            long responseTime = System.currentTimeMillis() - startTime;
            int statusCode = httpResponse.getStatus();
            
            MDC.put("statusCode", String.valueOf(statusCode));
            MDC.put("responseTime", String.valueOf(responseTime));
            
            if (statusCode >= 500) {
                logger.error("{} {} - {} ({}ms)", 
                    httpRequest.getMethod(), 
                    httpRequest.getRequestURI(),
                    statusCode,
                    responseTime);
            } else if (statusCode >= 400) {
                logger.warn("{} {} - {} ({}ms)", 
                    httpRequest.getMethod(), 
                    httpRequest.getRequestURI(),
                    statusCode,
                    responseTime);
            } else {
                logger.info("{} {} - {} ({}ms)", 
                    httpRequest.getMethod(), 
                    httpRequest.getRequestURI(),
                    statusCode,
                    responseTime);
            }
            
        } finally {
            // Clear MDC to prevent memory leaks
            MDC.clear();
        }
    }
}
