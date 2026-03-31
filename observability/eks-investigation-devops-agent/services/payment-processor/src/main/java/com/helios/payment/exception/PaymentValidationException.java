package com.helios.payment.exception;

import com.helios.payment.dto.ErrorResponse;
import lombok.Getter;

import java.util.List;

/**
 * Exception thrown when payment request validation fails.
 */
@Getter
public class PaymentValidationException extends RuntimeException {

    private final String errorCode;
    private final List<ErrorResponse.FieldError> fieldErrors;

    public PaymentValidationException(String message) {
        super(message);
        this.errorCode = "PAYMENT_VALIDATION_ERROR";
        this.fieldErrors = null;
    }

    public PaymentValidationException(String message, List<ErrorResponse.FieldError> fieldErrors) {
        super(message);
        this.errorCode = "PAYMENT_VALIDATION_ERROR";
        this.fieldErrors = fieldErrors;
    }

    public PaymentValidationException(String errorCode, String message, List<ErrorResponse.FieldError> fieldErrors) {
        super(message);
        this.errorCode = errorCode;
        this.fieldErrors = fieldErrors;
    }
}
