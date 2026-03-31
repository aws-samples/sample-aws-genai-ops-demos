package com.helios.payment.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.helios.payment.entity.Transaction;
import com.helios.payment.entity.TransactionStatus;
import lombok.*;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;

/**
 * DTO for transaction responses.
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
@Builder
@JsonInclude(JsonInclude.Include.NON_NULL)
public class TransactionResponse {

    private UUID id;
    private UUID merchantId;
    private BigDecimal amount;
    private String currency;
    private TransactionStatus status;
    private String paymentMethod;
    private String cardLastFour;
    private String cardBrand;
    private String description;
    private Map<String, Object> metadata;
    private String correlationId;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;

    public static TransactionResponse fromEntity(Transaction transaction) {
        return TransactionResponse.builder()
                .id(transaction.getId())
                .merchantId(transaction.getMerchantId())
                .amount(transaction.getAmount())
                .currency(transaction.getCurrency())
                .status(transaction.getStatus())
                .paymentMethod(transaction.getPaymentMethod())
                .cardLastFour(transaction.getCardLastFour())
                .cardBrand(transaction.getCardBrand())
                .description(transaction.getDescription())
                .metadata(transaction.getMetadata())
                .correlationId(transaction.getCorrelationId())
                .createdAt(transaction.getCreatedAt())
                .updatedAt(transaction.getUpdatedAt())
                .build();
    }
}
