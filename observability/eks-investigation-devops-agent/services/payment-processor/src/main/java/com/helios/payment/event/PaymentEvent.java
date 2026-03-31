package com.helios.payment.event;

import com.helios.payment.entity.TransactionStatus;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * Event published when a payment state changes.
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class PaymentEvent {
    
    private String eventId;
    private String eventType;
    private OffsetDateTime timestamp;
    private PaymentEventData data;

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class PaymentEventData {
        private UUID transactionId;
        private UUID merchantId;
        private BigDecimal amount;
        private String currency;
        private TransactionStatus previousStatus;
        private TransactionStatus currentStatus;
        private String paymentMethod;
        private String correlationId;
        private OffsetDateTime createdAt;
        private OffsetDateTime updatedAt;
    }
}
