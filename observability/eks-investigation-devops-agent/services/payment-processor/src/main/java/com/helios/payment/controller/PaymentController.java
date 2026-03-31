package com.helios.payment.controller;

import com.helios.payment.dto.PaymentRequest;
import com.helios.payment.dto.TransactionFilterRequest;
import com.helios.payment.dto.TransactionResponse;
import com.helios.payment.entity.Transaction;
import com.helios.payment.entity.TransactionStatus;
import com.helios.payment.service.PaymentService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Page;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * REST controller for payment operations.
 * 
 * Validates: Requirements 2.1, 2.2, 2.3, 2.4
 */
@RestController
@RequestMapping("/api/v1/payments")
@RequiredArgsConstructor
@Slf4j
public class PaymentController {

    private final PaymentService paymentService;

    /**
     * Create and authorize a payment.
     * POST /api/v1/payments/authorize
     */
    @PostMapping("/authorize")
    public ResponseEntity<TransactionResponse> authorize(
            @RequestHeader("X-Merchant-Id") UUID merchantId,
            @RequestHeader(value = "X-Correlation-Id", required = false) String correlationId,
            @RequestBody PaymentRequest request) {
        
        log.info("Authorize payment request for merchant: {}", merchantId);
        Transaction transaction = paymentService.authorize(merchantId, request, correlationId);
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(TransactionResponse.fromEntity(transaction));
    }

    /**
     * Capture an authorized payment.
     * POST /api/v1/payments/{id}/capture
     */
    @PostMapping("/{id}/capture")
    public ResponseEntity<TransactionResponse> capture(
            @RequestHeader("X-Merchant-Id") UUID merchantId,
            @PathVariable("id") UUID transactionId) {
        
        log.info("Capture payment request for transaction: {}", transactionId);
        Transaction transaction = paymentService.capture(merchantId, transactionId);
        return ResponseEntity.ok(TransactionResponse.fromEntity(transaction));
    }

    /**
     * Refund a captured payment.
     * POST /api/v1/payments/{id}/refund
     */
    @PostMapping("/{id}/refund")
    public ResponseEntity<TransactionResponse> refund(
            @RequestHeader("X-Merchant-Id") UUID merchantId,
            @PathVariable("id") UUID transactionId) {
        
        log.info("Refund payment request for transaction: {}", transactionId);
        Transaction transaction = paymentService.refund(merchantId, transactionId);
        return ResponseEntity.ok(TransactionResponse.fromEntity(transaction));
    }

    /**
     * Get transaction status.
     * GET /api/v1/payments/{id}
     */
    @GetMapping("/{id}")
    public ResponseEntity<TransactionResponse> getTransaction(
            @RequestHeader("X-Merchant-Id") UUID merchantId,
            @PathVariable("id") UUID transactionId) {
        
        log.info("Get transaction request: {}", transactionId);
        Transaction transaction = paymentService.getTransaction(merchantId, transactionId);
        return ResponseEntity.ok(TransactionResponse.fromEntity(transaction));
    }

    /**
     * List transactions with filters.
     * GET /api/v1/payments
     */
    @GetMapping
    public ResponseEntity<Page<TransactionResponse>> listTransactions(
            @RequestHeader("X-Merchant-Id") UUID merchantId,
            @RequestParam(required = false) TransactionStatus status,
            @RequestParam(required = false) OffsetDateTime startDate,
            @RequestParam(required = false) OffsetDateTime endDate,
            @RequestParam(required = false) BigDecimal minAmount,
            @RequestParam(required = false) BigDecimal maxAmount,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        
        log.info("List transactions request for merchant: {}", merchantId);
        
        TransactionFilterRequest filter = TransactionFilterRequest.builder()
                .status(status)
                .startDate(startDate)
                .endDate(endDate)
                .minAmount(minAmount)
                .maxAmount(maxAmount)
                .page(page)
                .size(size)
                .build();
        
        Page<Transaction> transactions = paymentService.listTransactions(merchantId, filter);
        Page<TransactionResponse> response = transactions.map(TransactionResponse::fromEntity);
        
        return ResponseEntity.ok(response);
    }
}
