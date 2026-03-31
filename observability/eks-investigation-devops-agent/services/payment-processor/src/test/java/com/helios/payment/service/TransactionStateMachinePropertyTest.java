package com.helios.payment.service;

import com.helios.payment.entity.TransactionStatus;
import com.helios.payment.exception.InvalidStateTransitionException;
import net.jqwik.api.*;
import net.jqwik.api.constraints.Size;

import java.util.List;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Property-based tests for Transaction State Machine.
 * 
 * Feature: devops-agent-eks, Property 2: Transaction State Machine Integrity
 * 
 * For any payment transaction, the state transitions SHALL follow the valid state machine:
 * PENDING → AUTHORIZED → CAPTURED → REFUNDED, or PENDING → AUTHORIZED → CANCELLED.
 * No invalid state transitions shall be permitted, and each transition SHALL persist
 * the updated state to the database.
 * 
 * Validates: Requirements 2.1, 2.2, 2.3, 2.4
 */
class TransactionStateMachinePropertyTest {

    private final TransactionStateMachine stateMachine = new TransactionStateMachine();

    // Define the valid transition paths
    private static final Set<TransactionStatus> TERMINAL_STATES = Set.of(
            TransactionStatus.REFUNDED,
            TransactionStatus.CANCELLED,
            TransactionStatus.FAILED
    );

    @Provide
    Arbitrary<TransactionStatus> anyStatus() {
        return Arbitraries.of(TransactionStatus.class);
    }

    @Provide
    Arbitrary<TransactionStatus> nonTerminalStatus() {
        return Arbitraries.of(
                TransactionStatus.PENDING,
                TransactionStatus.AUTHORIZED,
                TransactionStatus.CAPTURED
        );
    }

    /**
     * Property: Valid transitions are always accepted.
     * For any valid (current, target) pair, isValidTransition returns true.
     */
    @Property(tries = 100)
    void validTransitionsAreAccepted(
            @ForAll("validTransitionPairs") Tuple.Tuple2<TransactionStatus, TransactionStatus> pair) {
        TransactionStatus current = pair.get1();
        TransactionStatus target = pair.get2();
        
        assertThat(stateMachine.isValidTransition(current, target))
                .as("Transition from %s to %s should be valid", current, target)
                .isTrue();
    }

    @Provide
    Arbitrary<Tuple.Tuple2<TransactionStatus, TransactionStatus>> validTransitionPairs() {
        return Arbitraries.of(
                Tuple.of(TransactionStatus.PENDING, TransactionStatus.AUTHORIZED),
                Tuple.of(TransactionStatus.PENDING, TransactionStatus.FAILED),
                Tuple.of(TransactionStatus.AUTHORIZED, TransactionStatus.CAPTURED),
                Tuple.of(TransactionStatus.AUTHORIZED, TransactionStatus.CANCELLED),
                Tuple.of(TransactionStatus.AUTHORIZED, TransactionStatus.FAILED),
                Tuple.of(TransactionStatus.CAPTURED, TransactionStatus.REFUNDED),
                Tuple.of(TransactionStatus.CAPTURED, TransactionStatus.FAILED)
        );
    }

    /**
     * Property: Invalid transitions are always rejected.
     * For any invalid (current, target) pair, isValidTransition returns false.
     */
    @Property(tries = 100)
    void invalidTransitionsAreRejected(
            @ForAll("invalidTransitionPairs") Tuple.Tuple2<TransactionStatus, TransactionStatus> pair) {
        TransactionStatus current = pair.get1();
        TransactionStatus target = pair.get2();
        
        assertThat(stateMachine.isValidTransition(current, target))
                .as("Transition from %s to %s should be invalid", current, target)
                .isFalse();
    }

    @Provide
    Arbitrary<Tuple.Tuple2<TransactionStatus, TransactionStatus>> invalidTransitionPairs() {
        return Arbitraries.of(
                // Cannot go backwards
                Tuple.of(TransactionStatus.AUTHORIZED, TransactionStatus.PENDING),
                Tuple.of(TransactionStatus.CAPTURED, TransactionStatus.AUTHORIZED),
                Tuple.of(TransactionStatus.CAPTURED, TransactionStatus.PENDING),
                Tuple.of(TransactionStatus.REFUNDED, TransactionStatus.CAPTURED),
                // Cannot skip states
                Tuple.of(TransactionStatus.PENDING, TransactionStatus.CAPTURED),
                Tuple.of(TransactionStatus.PENDING, TransactionStatus.REFUNDED),
                Tuple.of(TransactionStatus.AUTHORIZED, TransactionStatus.REFUNDED),
                // Terminal states cannot transition
                Tuple.of(TransactionStatus.REFUNDED, TransactionStatus.AUTHORIZED),
                Tuple.of(TransactionStatus.CANCELLED, TransactionStatus.AUTHORIZED),
                Tuple.of(TransactionStatus.FAILED, TransactionStatus.AUTHORIZED),
                // Cannot cancel after capture
                Tuple.of(TransactionStatus.CAPTURED, TransactionStatus.CANCELLED),
                // Cannot refund cancelled
                Tuple.of(TransactionStatus.CANCELLED, TransactionStatus.REFUNDED)
        );
    }

    /**
     * Property: Terminal states have no valid outgoing transitions.
     * For any terminal state, getValidTransitions returns an empty set.
     */
    @Property(tries = 100)
    void terminalStatesHaveNoOutgoingTransitions(@ForAll("terminalStatus") TransactionStatus status) {
        Set<TransactionStatus> validTransitions = stateMachine.getValidTransitions(status);
        
        assertThat(validTransitions)
                .as("Terminal state %s should have no valid transitions", status)
                .isEmpty();
        
        assertThat(stateMachine.isTerminalState(status))
                .as("State %s should be recognized as terminal", status)
                .isTrue();
    }

    @Provide
    Arbitrary<TransactionStatus> terminalStatus() {
        return Arbitraries.of(
                TransactionStatus.REFUNDED,
                TransactionStatus.CANCELLED,
                TransactionStatus.FAILED
        );
    }

    /**
     * Property: Non-terminal states have at least one valid transition.
     * For any non-terminal state, getValidTransitions returns a non-empty set.
     */
    @Property(tries = 100)
    void nonTerminalStatesHaveOutgoingTransitions(@ForAll("nonTerminalStatus") TransactionStatus status) {
        Set<TransactionStatus> validTransitions = stateMachine.getValidTransitions(status);
        
        assertThat(validTransitions)
                .as("Non-terminal state %s should have valid transitions", status)
                .isNotEmpty();
        
        assertThat(stateMachine.isTerminalState(status))
                .as("State %s should not be recognized as terminal", status)
                .isFalse();
    }

    /**
     * Property: Invalid transitions throw InvalidStateTransitionException.
     * For any invalid transition, validateTransition throws the appropriate exception.
     */
    @Property(tries = 100)
    void invalidTransitionsThrowException(
            @ForAll("invalidTransitionPairs") Tuple.Tuple2<TransactionStatus, TransactionStatus> pair) {
        TransactionStatus current = pair.get1();
        TransactionStatus target = pair.get2();
        
        assertThatThrownBy(() -> stateMachine.validateTransition(current, target))
                .isInstanceOf(InvalidStateTransitionException.class)
                .hasMessageContaining(current.name())
                .hasMessageContaining(target.name());
    }

    /**
     * Property: Valid transitions do not throw exceptions.
     * For any valid transition, validateTransition completes without exception.
     */
    @Property(tries = 100)
    void validTransitionsDoNotThrow(
            @ForAll("validTransitionPairs") Tuple.Tuple2<TransactionStatus, TransactionStatus> pair) {
        TransactionStatus current = pair.get1();
        TransactionStatus target = pair.get2();
        
        // Should not throw
        stateMachine.validateTransition(current, target);
    }

    /**
     * Property: The happy path PENDING → AUTHORIZED → CAPTURED → REFUNDED is always valid.
     * This tests the complete successful payment flow.
     */
    @Property(tries = 100)
    void happyPathIsAlwaysValid() {
        assertThat(stateMachine.isValidTransition(TransactionStatus.PENDING, TransactionStatus.AUTHORIZED)).isTrue();
        assertThat(stateMachine.isValidTransition(TransactionStatus.AUTHORIZED, TransactionStatus.CAPTURED)).isTrue();
        assertThat(stateMachine.isValidTransition(TransactionStatus.CAPTURED, TransactionStatus.REFUNDED)).isTrue();
    }

    /**
     * Property: The cancellation path PENDING → AUTHORIZED → CANCELLED is always valid.
     * This tests the authorization cancellation flow.
     */
    @Property(tries = 100)
    void cancellationPathIsAlwaysValid() {
        assertThat(stateMachine.isValidTransition(TransactionStatus.PENDING, TransactionStatus.AUTHORIZED)).isTrue();
        assertThat(stateMachine.isValidTransition(TransactionStatus.AUTHORIZED, TransactionStatus.CANCELLED)).isTrue();
    }

    /**
     * Property: FAILED can be reached from any non-terminal state.
     * This ensures error handling is always possible.
     */
    @Property(tries = 100)
    void failedCanBeReachedFromNonTerminalStates(@ForAll("nonTerminalStatus") TransactionStatus status) {
        assertThat(stateMachine.isValidTransition(status, TransactionStatus.FAILED))
                .as("Should be able to transition from %s to FAILED", status)
                .isTrue();
    }

    /**
     * Property: Null inputs are handled gracefully.
     * isValidTransition returns false for null inputs.
     */
    @Property(tries = 100)
    void nullInputsReturnFalse(@ForAll("anyStatus") TransactionStatus status) {
        assertThat(stateMachine.isValidTransition(null, status)).isFalse();
        assertThat(stateMachine.isValidTransition(status, null)).isFalse();
        assertThat(stateMachine.isValidTransition(null, null)).isFalse();
    }
}
