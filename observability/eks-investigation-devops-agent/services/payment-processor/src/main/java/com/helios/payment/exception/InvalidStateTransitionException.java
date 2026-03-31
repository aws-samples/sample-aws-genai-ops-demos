package com.helios.payment.exception;

import com.helios.payment.entity.TransactionStatus;

/**
 * Exception thrown when an invalid state transition is attempted on a transaction.
 */
public class InvalidStateTransitionException extends RuntimeException {

    private final TransactionStatus currentStatus;
    private final TransactionStatus targetStatus;

    public InvalidStateTransitionException(TransactionStatus currentStatus, TransactionStatus targetStatus) {
        super(String.format("Invalid state transition from %s to %s", currentStatus, targetStatus));
        this.currentStatus = currentStatus;
        this.targetStatus = targetStatus;
    }

    public TransactionStatus getCurrentStatus() {
        return currentStatus;
    }

    public TransactionStatus getTargetStatus() {
        return targetStatus;
    }
}
