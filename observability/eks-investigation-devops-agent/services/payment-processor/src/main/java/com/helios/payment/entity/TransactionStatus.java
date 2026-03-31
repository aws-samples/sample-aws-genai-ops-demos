package com.helios.payment.entity;

/**
 * Represents the possible states of a payment transaction.
 * Valid transitions:
 * - PENDING → AUTHORIZED → CAPTURED → REFUNDED
 * - PENDING → AUTHORIZED → CANCELLED
 * - Any state → FAILED (on error)
 */
public enum TransactionStatus {
    PENDING,
    AUTHORIZED,
    CAPTURED,
    REFUNDED,
    CANCELLED,
    FAILED
}
