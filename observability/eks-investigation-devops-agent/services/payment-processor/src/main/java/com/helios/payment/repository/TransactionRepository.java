package com.helios.payment.repository;

import com.helios.payment.entity.Transaction;
import com.helios.payment.entity.TransactionStatus;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * Repository for Transaction entity with row-level security by merchant_id.
 * All queries enforce merchant_id filtering to ensure data isolation.
 */
@Repository
public interface TransactionRepository extends JpaRepository<Transaction, UUID> {

    /**
     * Find transaction by ID with merchant_id security filter.
     * Ensures merchants can only access their own transactions.
     */
    Optional<Transaction> findByIdAndMerchantId(UUID id, UUID merchantId);

    /**
     * Find all transactions for a merchant with pagination.
     */
    Page<Transaction> findByMerchantId(UUID merchantId, Pageable pageable);

    /**
     * Find transactions by merchant and status.
     */
    Page<Transaction> findByMerchantIdAndStatus(UUID merchantId, TransactionStatus status, Pageable pageable);

    /**
     * Find transactions by merchant within a date range.
     */
    @Query("SELECT t FROM Transaction t WHERE t.merchantId = :merchantId " +
           "AND t.createdAt >= :startDate AND t.createdAt <= :endDate")
    Page<Transaction> findByMerchantIdAndDateRange(
            @Param("merchantId") UUID merchantId,
            @Param("startDate") OffsetDateTime startDate,
            @Param("endDate") OffsetDateTime endDate,
            Pageable pageable);

    /**
     * Find transactions by merchant with multiple filter criteria.
     */
    @Query("SELECT t FROM Transaction t WHERE t.merchantId = :merchantId " +
           "AND (:status IS NULL OR t.status = :status) " +
           "AND (:startDate IS NULL OR t.createdAt >= :startDate) " +
           "AND (:endDate IS NULL OR t.createdAt <= :endDate) " +
           "AND (:minAmount IS NULL OR t.amount >= :minAmount) " +
           "AND (:maxAmount IS NULL OR t.amount <= :maxAmount)")
    Page<Transaction> findByMerchantIdWithFilters(
            @Param("merchantId") UUID merchantId,
            @Param("status") TransactionStatus status,
            @Param("startDate") OffsetDateTime startDate,
            @Param("endDate") OffsetDateTime endDate,
            @Param("minAmount") BigDecimal minAmount,
            @Param("maxAmount") BigDecimal maxAmount,
            Pageable pageable);

    /**
     * Find active transactions (CREATED or AUTHORIZED) for a merchant.
     */
    @Query("SELECT t FROM Transaction t WHERE t.merchantId = :merchantId " +
           "AND t.status IN ('CREATED', 'AUTHORIZED') ORDER BY t.createdAt DESC")
    List<Transaction> findActiveTransactionsByMerchantId(@Param("merchantId") UUID merchantId);

    /**
     * Count transactions by merchant and status.
     */
    long countByMerchantIdAndStatus(UUID merchantId, TransactionStatus status);
}
