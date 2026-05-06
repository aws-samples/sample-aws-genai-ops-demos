package com.helios.payment.service;

import com.helios.payment.dto.PaymentRequest;
import com.helios.payment.dto.TransactionFilterRequest;
import com.helios.payment.entity.Transaction;
import com.helios.payment.entity.TransactionEvent;
import com.helios.payment.entity.TransactionStatus;
import com.helios.payment.event.EventPublisher;
import com.helios.payment.exception.InvalidStateTransitionException;
import com.helios.payment.exception.TransactionNotFoundException;
import com.helios.payment.repository.TransactionEventRepository;
import com.helios.payment.repository.TransactionRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

/**
 * Service for payment operations.
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class PaymentService {

    private final TransactionRepository transactionRepository;
    private final TransactionEventRepository transactionEventRepository;
    private final TransactionStateMachine stateMachine;
    private final PaymentValidationService validationService;
    private final EventPublisher eventPublisher;

    /**
     * Create and authorize a payment.
     */
    @Transactional
    public Transaction authorize(UUID merchantId, PaymentRequest request, String correlationId) {
        validationService.validate(request);

        Transaction transaction = Transaction.builder()
                .merchantId(merchantId)
                .amount(request.getAmount())
                .currency(request.getCurrency())
                .paymentMethod(request.getPaymentMethodToken())
                .metadata(request.getMetadata())
                .correlationId(correlationId)
                .status(TransactionStatus.PENDING)
                .build();

        transaction = transactionRepository.save(transaction);
        log.info("Created transaction: {}", transaction.getId());
        recordEvent(transaction);

        // Simulate authorization
        TransactionStatus previousStatus = transaction.getStatus();
        stateMachine.validateTransition(transaction.getStatus(), TransactionStatus.AUTHORIZED);
        transaction.setStatus(TransactionStatus.AUTHORIZED);
        transaction = transactionRepository.save(transaction);
        
        log.info("Authorized transaction: {}", transaction.getId());
        recordEvent(transaction);
        
        // Publish event
        eventPublisher.publishStateChange(transaction, previousStatus);
        
        return transaction;
    }

    /**
     * Capture an authorized payment.
     */
    @Transactional
    public Transaction capture(UUID merchantId, UUID transactionId) {
        Transaction transaction = findByIdAndMerchantId(transactionId, merchantId);
        
        TransactionStatus previousStatus = transaction.getStatus();
        stateMachine.validateTransition(transaction.getStatus(), TransactionStatus.CAPTURED);
        transaction.setStatus(TransactionStatus.CAPTURED);
        transaction = transactionRepository.save(transaction);
        
        log.info("Captured transaction: {}", transaction.getId());
        recordEvent(transaction);
        
        // Publish event
        eventPublisher.publishStateChange(transaction, previousStatus);
        
        return transaction;
    }

    /**
     * Refund a captured payment.
     */
    @Transactional
    public Transaction refund(UUID merchantId, UUID transactionId) {
        Transaction transaction = findByIdAndMerchantId(transactionId, merchantId);
        
        TransactionStatus previousStatus = transaction.getStatus();
        stateMachine.validateTransition(transaction.getStatus(), TransactionStatus.REFUNDED);
        transaction.setStatus(TransactionStatus.REFUNDED);
        transaction = transactionRepository.save(transaction);
        
        log.info("Refunded transaction: {}", transaction.getId());
        recordEvent(transaction);
        
        // Publish event
        eventPublisher.publishStateChange(transaction, previousStatus);
        
        return transaction;
    }

    /**
     * Get a transaction by ID.
     */
    @Transactional(readOnly = true)
    public Transaction getTransaction(UUID merchantId, UUID transactionId) {
        return findByIdAndMerchantId(transactionId, merchantId);
    }

    /**
     * List transactions with filters.
     */
    @Transactional(readOnly = true)
    public Page<Transaction> listTransactions(UUID merchantId, TransactionFilterRequest filter) {
        PageRequest pageRequest = PageRequest.of(
                filter.getPage(),
                filter.getSize(),
                Sort.by(Sort.Direction.DESC, "createdAt")
        );

        // Use simple query when no filters are provided to avoid PostgreSQL type inference issues
        if (filter.getStatus() == null && filter.getStartDate() == null && 
            filter.getEndDate() == null && filter.getMinAmount() == null && 
            filter.getMaxAmount() == null) {
            return transactionRepository.findByMerchantId(merchantId, pageRequest);
        }

        // Use status-only query if only status filter is provided
        if (filter.getStatus() != null && filter.getStartDate() == null && 
            filter.getEndDate() == null && filter.getMinAmount() == null && 
            filter.getMaxAmount() == null) {
            return transactionRepository.findByMerchantIdAndStatus(merchantId, filter.getStatus(), pageRequest);
        }

        return transactionRepository.findByMerchantIdWithFilters(
                merchantId,
                filter.getStatus(),
                filter.getStartDate(),
                filter.getEndDate(),
                filter.getMinAmount(),
                filter.getMaxAmount(),
                pageRequest
        );
    }

    private Transaction findByIdAndMerchantId(UUID transactionId, UUID merchantId) {
        return transactionRepository.findByIdAndMerchantId(transactionId, merchantId)
                .orElseThrow(() -> new TransactionNotFoundException(transactionId));
    }

    /**
     * Record a state transition event for post-incident analysis.
     */
    private void recordEvent(Transaction transaction) {
        transactionEventRepository.save(TransactionEvent.builder()
                .transactionId(transaction.getId())
                .status(transaction.getStatus())
                .build());
    }
}
