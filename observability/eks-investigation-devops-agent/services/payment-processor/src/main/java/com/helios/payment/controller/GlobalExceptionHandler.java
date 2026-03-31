package com.helios.payment.controller;

import com.helios.payment.dto.ErrorResponse;
import com.helios.payment.exception.InvalidStateTransitionException;
import com.helios.payment.exception.PaymentValidationException;
import com.helios.payment.exception.TransactionNotFoundException;
import lombok.extern.slf4j.Slf4j;
import org.slf4j.MDC;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * Global exception handler for REST controllers.
 */
@RestControllerAdvice
@Slf4j
public class GlobalExceptionHandler {

    private static final String CORRELATION_ID_KEY = "correlationId";

    @ExceptionHandler(PaymentValidationException.class)
    public ResponseEntity<ErrorResponse> handlePaymentValidationException(PaymentValidationException ex) {
        log.warn("Payment validation error: {}", ex.getMessage());
        
        ErrorResponse response = ErrorResponse.of(
                ex.getErrorCode(),
                ex.getMessage(),
                getCorrelationId(),
                ex.getFieldErrors()
        );
        
        return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(response);
    }

    @ExceptionHandler(TransactionNotFoundException.class)
    public ResponseEntity<ErrorResponse> handleTransactionNotFoundException(TransactionNotFoundException ex) {
        log.warn("Transaction not found: {}", ex.getTransactionId());
        
        ErrorResponse response = ErrorResponse.of(
                "PAYMENT_NOT_FOUND",
                ex.getMessage(),
                getCorrelationId()
        );
        
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(response);
    }

    @ExceptionHandler(InvalidStateTransitionException.class)
    public ResponseEntity<ErrorResponse> handleInvalidStateTransitionException(InvalidStateTransitionException ex) {
        log.warn("Invalid state transition: {} -> {}", ex.getCurrentStatus(), ex.getTargetStatus());
        
        ErrorResponse response = ErrorResponse.of(
                "PAYMENT_INVALID_STATE",
                ex.getMessage(),
                getCorrelationId()
        );
        
        return ResponseEntity.status(HttpStatus.CONFLICT).body(response);
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ErrorResponse> handleGenericException(Exception ex) {
        log.error("Unexpected error", ex);
        
        ErrorResponse response = ErrorResponse.of(
                "INTERNAL_ERROR",
                "An unexpected error occurred",
                getCorrelationId()
        );
        
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(response);
    }

    private String getCorrelationId() {
        return MDC.get(CORRELATION_ID_KEY);
    }
}
