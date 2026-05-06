package com.helios.payment.entity;

import jakarta.persistence.*;
import lombok.*;

import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * JPA entity representing a transaction state transition event.
 * Each row records when a transaction moved to a given status.
 * Used by the MCP server's get_incident_impact tool for post-incident analysis.
 */
@Entity
@Table(name = "transaction_events")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class TransactionEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(name = "transaction_id", nullable = false)
    private UUID transactionId;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private TransactionStatus status;

    @Column(name = "occurred_at", updatable = false)
    private OffsetDateTime occurredAt;

    @PrePersist
    protected void onCreate() {
        occurredAt = OffsetDateTime.now();
    }
}
