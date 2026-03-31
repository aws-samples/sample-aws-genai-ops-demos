package com.helios.payment.service;

import com.helios.payment.dto.PaymentRequest;
import com.helios.payment.exception.PaymentValidationException;
import net.jqwik.api.*;
import net.jqwik.api.constraints.*;

import java.math.BigDecimal;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Property-based tests for Payment Validation Service.
 * 
 * Feature: devops-agent-eks, Property 3: Payment Request Validation
 * 
 * For any payment request with invalid data (missing required fields, invalid amount,
 * malformed token), the Payment_Processor SHALL return a response containing both
 * an error code and a descriptive error message, and SHALL NOT create a transaction record.
 * 
 * Validates: Requirements 2.7
 */
class PaymentValidationPropertyTest {

    private final PaymentValidationService validationService = new PaymentValidationService();

    private static final String[] SUPPORTED_CURRENCIES = {"EUR", "USD", "GBP", "CHF", "PLN", "CZK", "SEK", "NOK", "DKK"};

    @Provide
    Arbitrary<String> validCurrency() {
        return Arbitraries.of(SUPPORTED_CURRENCIES);
    }

    @Provide
    Arbitrary<BigDecimal> validAmount() {
        return Arbitraries.bigDecimals()
                .between(new BigDecimal("0.01"), new BigDecimal("9999999999.99"))
                .ofScale(2);
    }

    @Provide
    Arbitrary<String> validToken() {
        return Arbitraries.strings()
                .alpha()
                .numeric()
                .ofMinLength(1)
                .ofMaxLength(255);
    }

    @Provide
    Arbitrary<PaymentRequest> validPaymentRequest() {
        return Combinators.combine(validAmount(), validCurrency(), validToken())
                .as((amount, currency, token) -> PaymentRequest.builder()
                        .amount(amount)
                        .currency(currency)
                        .paymentMethodToken(token)
                        .build());
    }

    /**
     * Property: Valid payment requests pass validation.
     * For any payment request with valid amount, currency, and token, validation succeeds.
     */
    @Property(tries = 100)
    void validPaymentRequestsPassValidation(@ForAll("validPaymentRequest") PaymentRequest request) {
        // Should not throw
        validationService.validate(request);
        assertThat(validationService.isValid(request)).isTrue();
    }

    /**
     * Property: Null requests are rejected with error.
     */
    @Property(tries = 100)
    void nullRequestsAreRejected() {
        assertThatThrownBy(() -> validationService.validate(null))
                .isInstanceOf(PaymentValidationException.class)
                .hasMessageContaining("required");
    }

    /**
     * Property: Null amount is rejected with appropriate error.
     */
    @Property(tries = 100)
    void nullAmountIsRejected(
            @ForAll("validCurrency") String currency,
            @ForAll("validToken") String token) {
        PaymentRequest request = PaymentRequest.builder()
                .amount(null)
                .currency(currency)
                .paymentMethodToken(token)
                .build();

        assertThatThrownBy(() -> validationService.validate(request))
                .isInstanceOf(PaymentValidationException.class)
                .satisfies(ex -> {
                    PaymentValidationException pve = (PaymentValidationException) ex;
                    assertThat(pve.getErrorCode()).isEqualTo("PAYMENT_VALIDATION_ERROR");
                    assertThat(pve.getFieldErrors()).isNotNull();
                    assertThat(pve.getFieldErrors())
                            .anyMatch(e -> e.getField().equals("amount"));
                });
    }

    /**
     * Property: Zero or negative amounts are rejected.
     * For any amount <= 0, validation fails with appropriate error.
     */
    @Property(tries = 100)
    void zeroOrNegativeAmountsAreRejected(
            @ForAll @BigRange(max = "0.00") BigDecimal invalidAmount,
            @ForAll("validCurrency") String currency,
            @ForAll("validToken") String token) {
        PaymentRequest request = PaymentRequest.builder()
                .amount(invalidAmount)
                .currency(currency)
                .paymentMethodToken(token)
                .build();

        assertThatThrownBy(() -> validationService.validate(request))
                .isInstanceOf(PaymentValidationException.class)
                .satisfies(ex -> {
                    PaymentValidationException pve = (PaymentValidationException) ex;
                    assertThat(pve.getFieldErrors())
                            .anyMatch(e -> e.getField().equals("amount") && 
                                          e.getIssue().contains("greater than zero"));
                });
    }

    /**
     * Property: Null or blank currency is rejected.
     */
    @Property(tries = 100)
    void nullOrBlankCurrencyIsRejected(
            @ForAll("validAmount") BigDecimal amount,
            @ForAll("validToken") String token) {
        // Test null currency
        PaymentRequest nullCurrencyRequest = PaymentRequest.builder()
                .amount(amount)
                .currency(null)
                .paymentMethodToken(token)
                .build();

        assertThatThrownBy(() -> validationService.validate(nullCurrencyRequest))
                .isInstanceOf(PaymentValidationException.class)
                .satisfies(ex -> {
                    PaymentValidationException pve = (PaymentValidationException) ex;
                    assertThat(pve.getFieldErrors())
                            .anyMatch(e -> e.getField().equals("currency"));
                });

        // Test blank currency
        PaymentRequest blankCurrencyRequest = PaymentRequest.builder()
                .amount(amount)
                .currency("   ")
                .paymentMethodToken(token)
                .build();

        assertThatThrownBy(() -> validationService.validate(blankCurrencyRequest))
                .isInstanceOf(PaymentValidationException.class);
    }

    /**
     * Property: Invalid currency format is rejected.
     * For any currency that doesn't match the 3-letter ISO pattern, validation fails.
     */
    @Property(tries = 100)
    void invalidCurrencyFormatIsRejected(
            @ForAll("invalidCurrencyFormat") String invalidCurrency,
            @ForAll("validAmount") BigDecimal amount,
            @ForAll("validToken") String token) {
        PaymentRequest request = PaymentRequest.builder()
                .amount(amount)
                .currency(invalidCurrency)
                .paymentMethodToken(token)
                .build();

        assertThatThrownBy(() -> validationService.validate(request))
                .isInstanceOf(PaymentValidationException.class)
                .satisfies(ex -> {
                    PaymentValidationException pve = (PaymentValidationException) ex;
                    assertThat(pve.getFieldErrors())
                            .anyMatch(e -> e.getField().equals("currency"));
                });
    }

    @Provide
    Arbitrary<String> invalidCurrencyFormat() {
        return Arbitraries.oneOf(
                Arbitraries.strings().alpha().ofLength(2),  // Too short
                Arbitraries.strings().alpha().ofLength(4),  // Too long
                Arbitraries.strings().numeric().ofLength(3), // Numbers only
                Arbitraries.just("eur"),  // Lowercase
                Arbitraries.just("Eur"),  // Mixed case
                Arbitraries.just("E1R")   // Contains number
        );
    }

    /**
     * Property: Unsupported currencies are rejected.
     */
    @Property(tries = 100)
    void unsupportedCurrenciesAreRejected(
            @ForAll("unsupportedCurrency") String unsupportedCurrency,
            @ForAll("validAmount") BigDecimal amount,
            @ForAll("validToken") String token) {
        PaymentRequest request = PaymentRequest.builder()
                .amount(amount)
                .currency(unsupportedCurrency)
                .paymentMethodToken(token)
                .build();

        assertThatThrownBy(() -> validationService.validate(request))
                .isInstanceOf(PaymentValidationException.class)
                .satisfies(ex -> {
                    PaymentValidationException pve = (PaymentValidationException) ex;
                    assertThat(pve.getFieldErrors())
                            .anyMatch(e -> e.getField().equals("currency") && 
                                          e.getIssue().contains("not supported"));
                });
    }

    @Provide
    Arbitrary<String> unsupportedCurrency() {
        return Arbitraries.of("JPY", "CNY", "INR", "BRL", "AUD", "CAD", "MXN", "RUB");
    }

    /**
     * Property: Null or blank payment method token is rejected.
     */
    @Property(tries = 100)
    void nullOrBlankTokenIsRejected(
            @ForAll("validAmount") BigDecimal amount,
            @ForAll("validCurrency") String currency) {
        // Test null token
        PaymentRequest nullTokenRequest = PaymentRequest.builder()
                .amount(amount)
                .currency(currency)
                .paymentMethodToken(null)
                .build();

        assertThatThrownBy(() -> validationService.validate(nullTokenRequest))
                .isInstanceOf(PaymentValidationException.class)
                .satisfies(ex -> {
                    PaymentValidationException pve = (PaymentValidationException) ex;
                    assertThat(pve.getFieldErrors())
                            .anyMatch(e -> e.getField().equals("paymentMethodToken"));
                });

        // Test blank token
        PaymentRequest blankTokenRequest = PaymentRequest.builder()
                .amount(amount)
                .currency(currency)
                .paymentMethodToken("   ")
                .build();

        assertThatThrownBy(() -> validationService.validate(blankTokenRequest))
                .isInstanceOf(PaymentValidationException.class);
    }

    /**
     * Property: Token exceeding max length is rejected.
     */
    @Property(tries = 100)
    void tokenExceedingMaxLengthIsRejected(
            @ForAll("validAmount") BigDecimal amount,
            @ForAll("validCurrency") String currency,
            @ForAll @StringLength(min = 256, max = 500) String longToken) {
        PaymentRequest request = PaymentRequest.builder()
                .amount(amount)
                .currency(currency)
                .paymentMethodToken(longToken)
                .build();

        assertThatThrownBy(() -> validationService.validate(request))
                .isInstanceOf(PaymentValidationException.class)
                .satisfies(ex -> {
                    PaymentValidationException pve = (PaymentValidationException) ex;
                    assertThat(pve.getFieldErrors())
                            .anyMatch(e -> e.getField().equals("paymentMethodToken") && 
                                          e.getIssue().contains("255"));
                });
    }

    /**
     * Property: Multiple validation errors are collected.
     * When multiple fields are invalid, all errors are reported.
     */
    @Property(tries = 100)
    void multipleValidationErrorsAreCollected() {
        PaymentRequest request = PaymentRequest.builder()
                .amount(BigDecimal.ZERO)
                .currency("invalid")
                .paymentMethodToken("")
                .build();

        assertThatThrownBy(() -> validationService.validate(request))
                .isInstanceOf(PaymentValidationException.class)
                .satisfies(ex -> {
                    PaymentValidationException pve = (PaymentValidationException) ex;
                    assertThat(pve.getFieldErrors()).hasSizeGreaterThanOrEqualTo(3);
                });
    }

    /**
     * Property: Validation exception contains error code.
     * For any invalid request, the exception contains a non-null error code.
     */
    @Property(tries = 100)
    void validationExceptionContainsErrorCode(
            @ForAll("invalidPaymentRequest") PaymentRequest invalidRequest) {
        assertThatThrownBy(() -> validationService.validate(invalidRequest))
                .isInstanceOf(PaymentValidationException.class)
                .satisfies(ex -> {
                    PaymentValidationException pve = (PaymentValidationException) ex;
                    assertThat(pve.getErrorCode()).isNotNull().isNotBlank();
                });
    }

    @Provide
    Arbitrary<PaymentRequest> invalidPaymentRequest() {
        return Arbitraries.oneOf(
                // Null amount
                Combinators.combine(validCurrency(), validToken())
                        .as((currency, token) -> PaymentRequest.builder()
                                .amount(null)
                                .currency(currency)
                                .paymentMethodToken(token)
                                .build()),
                // Zero amount
                Combinators.combine(validCurrency(), validToken())
                        .as((currency, token) -> PaymentRequest.builder()
                                .amount(BigDecimal.ZERO)
                                .currency(currency)
                                .paymentMethodToken(token)
                                .build()),
                // Invalid currency
                Combinators.combine(validAmount(), validToken())
                        .as((amount, token) -> PaymentRequest.builder()
                                .amount(amount)
                                .currency("XXX")
                                .paymentMethodToken(token)
                                .build()),
                // Blank token
                Combinators.combine(validAmount(), validCurrency())
                        .as((amount, currency) -> PaymentRequest.builder()
                                .amount(amount)
                                .currency(currency)
                                .paymentMethodToken("")
                                .build())
        );
    }
}
