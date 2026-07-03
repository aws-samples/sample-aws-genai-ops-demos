package com.helios.payment.service;

import com.helios.payment.dto.TransactionFilterRequest;
import com.helios.payment.entity.Transaction;
import com.helios.payment.entity.TransactionStatus;
import com.helios.payment.event.EventPublisher;
import com.helios.payment.repository.TransactionEventRepository;
import com.helios.payment.repository.TransactionRepository;
import net.jqwik.api.*;
import net.jqwik.api.constraints.IntRange;
import org.mockito.ArgumentCaptor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.stream.Collectors;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

/**
 * Property-based tests for Transaction Filtering.
 * 
 * Feature: devops-agent-eks, Property 9: Transaction History Filtering
 * 
 * For any merchant querying their transaction history with filter parameters
 * (status, date range, amount range), the returned results SHALL contain only
 * transactions matching ALL specified filter criteria and belonging to that merchant.
 * 
 * Validates: Requirements 1.6
 */
class TransactionFilteringPropertyTest {

    private TransactionRepository transactionRepository;
    private PaymentService paymentService;

    void setUp() {
        transactionRepository = mock(TransactionRepository.class);
        TransactionEventRepository transactionEventRepository = mock(TransactionEventRepository.class);
        TransactionStateMachine stateMachine = new TransactionStateMachine();
        PaymentValidationService validationService = new PaymentValidationService();
        EventPublisher eventPublisher = mock(EventPublisher.class);
        paymentService = new PaymentService(transactionRepository, transactionEventRepository, stateMachine, validationService, eventPublisher);
    }

    @Provide
    Arbitrary<TransactionStatus> anyStatus() {
        return Arbitraries.of(TransactionStatus.class);
    }

    @Provide
    Arbitrary<BigDecimal> validAmount() {
        return Arbitraries.bigDecimals()
                .between(new BigDecimal("0.01"), new BigDecimal("10000.00"))
                .ofScale(2);
    }

    @Provide
    Arbitrary<UUID> anyMerchantId() {
        return Arbitraries.create(UUID::randomUUID);
    }

    /**
     * Property: Filter parameters are correctly passed to repository.
     * For any filter combination, the repository receives the exact filter values.
     */
    @Property(tries = 100)
    void filterParametersAreCorrectlyPassedToRepository(
            @ForAll("anyMerchantId") UUID merchantId,
            @ForAll("anyStatus") TransactionStatus filterStatus) {
        setUp();
        
        // Mock repository to return empty page
        when(transactionRepository.findByMerchantIdWithFilters(
                any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new PageImpl<>(List.of()));

        TransactionFilterRequest filter = TransactionFilterRequest.builder()
                .status(filterStatus)
                .page(0)
                .size(100)
                .build();

        paymentService.listTransactions(merchantId, filter);

        // Verify repository was called with correct parameters
        ArgumentCaptor<UUID> merchantCaptor = ArgumentCaptor.forClass(UUID.class);
        ArgumentCaptor<TransactionStatus> statusCaptor = ArgumentCaptor.forClass(TransactionStatus.class);
        
        verify(transactionRepository).findByMerchantIdWithFilters(
                merchantCaptor.capture(),
                statusCaptor.capture(),
                any(), any(), any(), any(), any());

        assertThat(merchantCaptor.getValue()).isEqualTo(merchantId);
        assertThat(statusCaptor.getValue()).isEqualTo(filterStatus);
    }

    /**
     * Property: Amount range filters are correctly passed to repository.
     * For any min/max amount filter, the repository receives the exact values.
     */
    @Property(tries = 100)
    void amountRangeFiltersAreCorrectlyPassed(
            @ForAll("anyMerchantId") UUID merchantId,
            @ForAll("validAmount") BigDecimal minAmount,
            @ForAll("validAmount") BigDecimal maxAmount) {
        setUp();
        
        BigDecimal actualMin = minAmount.min(maxAmount);
        BigDecimal actualMax = minAmount.max(maxAmount);

        when(transactionRepository.findByMerchantIdWithFilters(
                any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new PageImpl<>(List.of()));

        TransactionFilterRequest filter = TransactionFilterRequest.builder()
                .minAmount(actualMin)
                .maxAmount(actualMax)
                .page(0)
                .size(100)
                .build();

        paymentService.listTransactions(merchantId, filter);

        ArgumentCaptor<BigDecimal> minCaptor = ArgumentCaptor.forClass(BigDecimal.class);
        ArgumentCaptor<BigDecimal> maxCaptor = ArgumentCaptor.forClass(BigDecimal.class);
        
        verify(transactionRepository).findByMerchantIdWithFilters(
                eq(merchantId), any(), any(), any(),
                minCaptor.capture(), maxCaptor.capture(), any());

        assertThat(minCaptor.getValue()).isEqualByComparingTo(actualMin);
        assertThat(maxCaptor.getValue()).isEqualByComparingTo(actualMax);
    }

    /**
     * Property: Pagination parameters are correctly applied.
     * For any page/size combination, the pageable is correctly constructed.
     */
    @Property(tries = 100)
    void paginationParametersAreCorrectlyApplied(
            @ForAll("anyMerchantId") UUID merchantId,
            @ForAll @IntRange(min = 0, max = 100) int page,
            @ForAll @IntRange(min = 1, max = 100) int size) {
        setUp();
        
        when(transactionRepository.findByMerchantIdWithFilters(
                any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new PageImpl<>(List.of()));

        TransactionFilterRequest filter = TransactionFilterRequest.builder()
                .page(page)
                .size(size)
                .build();

        paymentService.listTransactions(merchantId, filter);

        ArgumentCaptor<Pageable> pageableCaptor = ArgumentCaptor.forClass(Pageable.class);
        
        verify(transactionRepository).findByMerchantIdWithFilters(
                any(), any(), any(), any(), any(), any(),
                pageableCaptor.capture());

        Pageable captured = pageableCaptor.getValue();
        assertThat(captured.getPageNumber()).isEqualTo(page);
        assertThat(captured.getPageSize()).isEqualTo(size);
    }

    /**
     * Property: Results are sorted by creation date descending.
     * The pageable always specifies descending sort by createdAt.
     */
    @Property(tries = 100)
    void resultsAreSortedByCreationDateDescending(@ForAll("anyMerchantId") UUID merchantId) {
        setUp();
        
        when(transactionRepository.findByMerchantIdWithFilters(
                any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new PageImpl<>(List.of()));

        TransactionFilterRequest filter = TransactionFilterRequest.builder()
                .page(0)
                .size(100)
                .build();

        paymentService.listTransactions(merchantId, filter);

        ArgumentCaptor<Pageable> pageableCaptor = ArgumentCaptor.forClass(Pageable.class);
        
        verify(transactionRepository).findByMerchantIdWithFilters(
                any(), any(), any(), any(), any(), any(),
                pageableCaptor.capture());

        Pageable captured = pageableCaptor.getValue();
        assertThat(captured.getSort().getOrderFor("createdAt")).isNotNull();
        assertThat(captured.getSort().getOrderFor("createdAt").isDescending()).isTrue();
    }

    /**
     * Property: Empty filter returns all merchant transactions.
     * When no filters are specified, null values are passed for optional filters.
     */
    @Property(tries = 100)
    void emptyFilterPassesNullForOptionalParameters(@ForAll("anyMerchantId") UUID merchantId) {
        setUp();
        
        when(transactionRepository.findByMerchantIdWithFilters(
                any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new PageImpl<>(List.of()));

        TransactionFilterRequest filter = TransactionFilterRequest.builder()
                .page(0)
                .size(100)
                .build();

        paymentService.listTransactions(merchantId, filter);

        verify(transactionRepository).findByMerchantIdWithFilters(
                eq(merchantId),
                isNull(),  // status
                isNull(),  // startDate
                isNull(),  // endDate
                isNull(),  // minAmount
                isNull(),  // maxAmount
                any());
    }

    /**
     * Property: Service returns exactly what repository returns.
     * The service does not modify the results from the repository.
     */
    @Property(tries = 100)
    void serviceReturnsExactlyWhatRepositoryReturns(
            @ForAll("anyMerchantId") UUID merchantId,
            @ForAll @IntRange(min = 0, max = 10) int transactionCount) {
        setUp();
        
        List<Transaction> expectedTransactions = new ArrayList<>();
        for (int i = 0; i < transactionCount; i++) {
            expectedTransactions.add(createTransaction(merchantId, TransactionStatus.AUTHORIZED));
        }
        Page<Transaction> expectedPage = new PageImpl<>(expectedTransactions);

        when(transactionRepository.findByMerchantIdWithFilters(
                any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(expectedPage);

        TransactionFilterRequest filter = TransactionFilterRequest.builder()
                .page(0)
                .size(100)
                .build();

        Page<Transaction> result = paymentService.listTransactions(merchantId, filter);

        assertThat(result.getContent()).hasSize(transactionCount);
        assertThat(result.getContent()).containsExactlyElementsOf(expectedTransactions);
    }

    /**
     * Property: Merchant ID is always enforced in queries.
     * For any filter combination, the merchant ID is always passed to the repository.
     */
    @Property(tries = 100)
    void merchantIdIsAlwaysEnforcedInQueries(
            @ForAll("anyMerchantId") UUID merchantId,
            @ForAll("anyStatus") TransactionStatus status,
            @ForAll("validAmount") BigDecimal amount) {
        setUp();
        
        when(transactionRepository.findByMerchantIdWithFilters(
                any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new PageImpl<>(List.of()));

        TransactionFilterRequest filter = TransactionFilterRequest.builder()
                .status(status)
                .minAmount(amount)
                .page(0)
                .size(100)
                .build();

        paymentService.listTransactions(merchantId, filter);

        ArgumentCaptor<UUID> merchantCaptor = ArgumentCaptor.forClass(UUID.class);
        
        verify(transactionRepository).findByMerchantIdWithFilters(
                merchantCaptor.capture(),
                any(), any(), any(), any(), any(), any());

        assertThat(merchantCaptor.getValue()).isEqualTo(merchantId);
    }

    // Helper method
    private Transaction createTransaction(UUID merchantId, TransactionStatus status) {
        return Transaction.builder()
                .id(UUID.randomUUID())
                .merchantId(merchantId)
                .amount(new BigDecimal("100.00"))
                .currency("EUR")
                .paymentMethod("card")
                .status(status)
                .createdAt(OffsetDateTime.now())
                .build();
    }
}
