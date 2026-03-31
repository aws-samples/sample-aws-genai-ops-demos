package com.helios.payment.service;

import com.helios.payment.dto.ErrorResponse;
import com.helios.payment.dto.PaymentRequest;
import com.helios.payment.exception.PaymentValidationException;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Service for validating payment requests.
 * 
 * Property 3: Payment Request Validation
 * For any payment request with invalid data (missing required fields, invalid amount,
 * malformed token), the Payment_Processor SHALL return a response containing both
 * an error code and a descriptive error message, and SHALL NOT create a transaction record.
 * 
 * Validates: Requirements 2.7
 */
@Service
public class PaymentValidationService {

    private static final Pattern CURRENCY_PATTERN = Pattern.compile("^[A-Z]{3}$");
    private static final Set<String> SUPPORTED_CURRENCIES = Set.of(
            "EUR", "USD", "GBP", "CHF", "PLN", "CZK", "SEK", "NOK", "DKK"
    );
    private static final int MAX_TOKEN_LENGTH = 255;
    private static final BigDecimal MIN_AMOUNT = new BigDecimal("0.01");
    private static final BigDecimal MAX_AMOUNT = new BigDecimal("9999999999.99");

    /**
     * Validate a payment request.
     * 
     * @param request The payment request to validate
     * @throws PaymentValidationException if validation fails
     */
    public void validate(PaymentRequest request) {
        List<ErrorResponse.FieldError> errors = new ArrayList<>();

        if (request == null) {
            throw new PaymentValidationException("Payment request is required");
        }

        validateAmount(request.getAmount(), errors);
        validateCurrency(request.getCurrency(), errors);
        validatePaymentMethodToken(request.getPaymentMethodToken(), errors);

        if (!errors.isEmpty()) {
            throw new PaymentValidationException(
                    "Payment request validation failed",
                    errors
            );
        }
    }

    /**
     * Validate the payment amount.
     */
    private void validateAmount(BigDecimal amount, List<ErrorResponse.FieldError> errors) {
        if (amount == null) {
            errors.add(ErrorResponse.FieldError.builder()
                    .field("amount")
                    .issue("Amount is required")
                    .build());
            return;
        }

        if (amount.compareTo(MIN_AMOUNT) < 0) {
            errors.add(ErrorResponse.FieldError.builder()
                    .field("amount")
                    .issue("Amount must be greater than zero")
                    .build());
        }

        if (amount.compareTo(MAX_AMOUNT) > 0) {
            errors.add(ErrorResponse.FieldError.builder()
                    .field("amount")
                    .issue("Amount exceeds maximum allowed value")
                    .build());
        }

        if (amount.scale() > 2) {
            errors.add(ErrorResponse.FieldError.builder()
                    .field("amount")
                    .issue("Amount must have at most 2 decimal places")
                    .build());
        }
    }

    /**
     * Validate the currency code.
     */
    private void validateCurrency(String currency, List<ErrorResponse.FieldError> errors) {
        if (currency == null || currency.isBlank()) {
            errors.add(ErrorResponse.FieldError.builder()
                    .field("currency")
                    .issue("Currency is required")
                    .build());
            return;
        }

        if (!CURRENCY_PATTERN.matcher(currency).matches()) {
            errors.add(ErrorResponse.FieldError.builder()
                    .field("currency")
                    .issue("Currency must be a valid 3-letter ISO code")
                    .build());
            return;
        }

        if (!SUPPORTED_CURRENCIES.contains(currency)) {
            errors.add(ErrorResponse.FieldError.builder()
                    .field("currency")
                    .issue("Currency is not supported")
                    .build());
        }
    }

    /**
     * Validate the payment method token.
     * Note: Length is checked before blank to properly report "too long" for whitespace-only tokens
     */
    private void validatePaymentMethodToken(String token, List<ErrorResponse.FieldError> errors) {
        if (token == null) {
            errors.add(ErrorResponse.FieldError.builder()
                    .field("paymentMethodToken")
                    .issue("Payment method token is required")
                    .build());
            return;
        }

        // Check length first (before trimming) to properly report "too long" for whitespace-only tokens
        if (token.length() > MAX_TOKEN_LENGTH) {
            errors.add(ErrorResponse.FieldError.builder()
                    .field("paymentMethodToken")
                    .issue("Payment method token must not exceed 255 characters")
                    .build());
            return;
        }

        if (token.isBlank()) {
            errors.add(ErrorResponse.FieldError.builder()
                    .field("paymentMethodToken")
                    .issue("Payment method token is required")
                    .build());
        }
    }

    /**
     * Check if a payment request is valid without throwing an exception.
     * 
     * @param request The payment request to validate
     * @return true if valid, false otherwise
     */
    public boolean isValid(PaymentRequest request) {
        try {
            validate(request);
            return true;
        } catch (PaymentValidationException e) {
            return false;
        }
    }
}
