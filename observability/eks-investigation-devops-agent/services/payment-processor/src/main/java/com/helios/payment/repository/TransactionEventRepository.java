package com.helios.payment.repository;

import com.helios.payment.entity.TransactionEvent;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.UUID;

/**
 * Repository for TransactionEvent entity.
 * Stores state transition events for post-incident analysis.
 */
@Repository
public interface TransactionEventRepository extends JpaRepository<TransactionEvent, UUID> {
}
