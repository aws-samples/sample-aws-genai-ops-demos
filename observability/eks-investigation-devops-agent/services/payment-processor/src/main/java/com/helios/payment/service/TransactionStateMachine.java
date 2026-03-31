package com.helios.payment.service;

import com.helios.payment.entity.TransactionStatus;
import com.helios.payment.exception.InvalidStateTransitionException;
import org.springframework.stereotype.Component;

import java.util.EnumMap;
import java.util.EnumSet;
import java.util.Map;
import java.util.Set;

/**
 * Transaction state machine that enforces valid state transitions.
 * 
 * Valid transitions:
 * - PENDING → AUTHORIZED (authorize payment)
 * - AUTHORIZED → CAPTURED (capture funds)
 * - AUTHORIZED → CANCELLED (cancel authorization)
 * - CAPTURED → REFUNDED (refund payment)
 * - Any state → FAILED (on error)
 * 
 * Property 2: Transaction State Machine Integrity
 * Validates: Requirements 2.1, 2.2, 2.3, 2.4
 */
@Component
public class TransactionStateMachine {

    private static final Map<TransactionStatus, Set<TransactionStatus>> VALID_TRANSITIONS;

    static {
        VALID_TRANSITIONS = new EnumMap<>(TransactionStatus.class);
        
        // PENDING can transition to AUTHORIZED or FAILED
        VALID_TRANSITIONS.put(TransactionStatus.PENDING, 
                EnumSet.of(TransactionStatus.AUTHORIZED, TransactionStatus.FAILED));
        
        // AUTHORIZED can transition to CAPTURED, CANCELLED, or FAILED
        VALID_TRANSITIONS.put(TransactionStatus.AUTHORIZED, 
                EnumSet.of(TransactionStatus.CAPTURED, TransactionStatus.CANCELLED, TransactionStatus.FAILED));
        
        // CAPTURED can transition to REFUNDED or FAILED
        VALID_TRANSITIONS.put(TransactionStatus.CAPTURED, 
                EnumSet.of(TransactionStatus.REFUNDED, TransactionStatus.FAILED));
        
        // Terminal states - no further transitions allowed (except FAILED is always terminal)
        VALID_TRANSITIONS.put(TransactionStatus.REFUNDED, EnumSet.noneOf(TransactionStatus.class));
        VALID_TRANSITIONS.put(TransactionStatus.CANCELLED, EnumSet.noneOf(TransactionStatus.class));
        VALID_TRANSITIONS.put(TransactionStatus.FAILED, EnumSet.noneOf(TransactionStatus.class));
    }

    /**
     * Check if a state transition is valid.
     * 
     * @param currentStatus The current status of the transaction
     * @param targetStatus The desired target status
     * @return true if the transition is valid, false otherwise
     */
    public boolean isValidTransition(TransactionStatus currentStatus, TransactionStatus targetStatus) {
        if (currentStatus == null || targetStatus == null) {
            return false;
        }
        
        Set<TransactionStatus> validTargets = VALID_TRANSITIONS.get(currentStatus);
        return validTargets != null && validTargets.contains(targetStatus);
    }

    /**
     * Validate and perform a state transition.
     * 
     * @param currentStatus The current status of the transaction
     * @param targetStatus The desired target status
     * @throws InvalidStateTransitionException if the transition is not valid
     */
    public void validateTransition(TransactionStatus currentStatus, TransactionStatus targetStatus) {
        if (!isValidTransition(currentStatus, targetStatus)) {
            throw new InvalidStateTransitionException(currentStatus, targetStatus);
        }
    }

    /**
     * Get all valid target states from a given status.
     * 
     * @param currentStatus The current status
     * @return Set of valid target states
     */
    public Set<TransactionStatus> getValidTransitions(TransactionStatus currentStatus) {
        if (currentStatus == null) {
            return EnumSet.noneOf(TransactionStatus.class);
        }
        Set<TransactionStatus> transitions = VALID_TRANSITIONS.get(currentStatus);
        return transitions != null ? EnumSet.copyOf(transitions) : EnumSet.noneOf(TransactionStatus.class);
    }

    /**
     * Check if a status is a terminal state (no further transitions possible).
     * 
     * @param status The status to check
     * @return true if the status is terminal
     */
    public boolean isTerminalState(TransactionStatus status) {
        if (status == null) {
            return false;
        }
        Set<TransactionStatus> transitions = VALID_TRANSITIONS.get(status);
        return transitions == null || transitions.isEmpty();
    }
}
