package com.helios.payment.event;

import com.helios.payment.entity.Transaction;
import com.helios.payment.entity.TransactionStatus;

/**
 * Interface for publishing payment events.
 */
public interface EventPublisher {
    
    /**
     * Publish a payment state change event.
     */
    void publishStateChange(Transaction transaction, TransactionStatus previousStatus);
}
