package com.helios.payment.dto;

import com.helios.payment.entity.TransactionStatus;
import lombok.*;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

/**
 * DTO for transaction filter parameters.
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class TransactionFilterRequest {

    private TransactionStatus status;
    private OffsetDateTime startDate;
    private OffsetDateTime endDate;
    private BigDecimal minAmount;
    private BigDecimal maxAmount;
    
    @Builder.Default
    private int page = 0;
    
    @Builder.Default
    private int size = 20;
}
