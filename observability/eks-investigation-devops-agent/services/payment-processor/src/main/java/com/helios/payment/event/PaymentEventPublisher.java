package com.helios.payment.event;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.helios.payment.entity.Transaction;
import com.helios.payment.entity.TransactionStatus;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.SendMessageRequest;
import software.amazon.awssdk.services.sqs.model.SendMessageResponse;

import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * Service for publishing payment state change events to SQS.
 */
@Service
@Slf4j
public class PaymentEventPublisher implements EventPublisher {

    private final SqsClient sqsClient;
    private final String queueUrl;
    private final ObjectMapper objectMapper;
    private final boolean enabled;

    public PaymentEventPublisher(
            SqsClient sqsClient,
            @Value("${aws.sqs.payment-events-queue-url:}") String queueUrl,
            @Value("${aws.sqs.enabled:true}") boolean enabled) {
        this.sqsClient = sqsClient;
        this.queueUrl = queueUrl;
        this.enabled = enabled;
        this.objectMapper = new ObjectMapper();
        this.objectMapper.registerModule(new JavaTimeModule());
    }

    /**
     * Publish a payment state change event.
     */
    public void publishStateChange(Transaction transaction, TransactionStatus previousStatus) {
        if (!enabled || queueUrl == null || queueUrl.isBlank()) {
            log.debug("SQS publishing disabled or queue URL not configured, skipping event publish");
            return;
        }

        PaymentEvent event = buildEvent(transaction, previousStatus);
        
        try {
            String messageBody = objectMapper.writeValueAsString(event);
            
            SendMessageRequest request = SendMessageRequest.builder()
                    .queueUrl(queueUrl)
                    .messageBody(messageBody)
                    .messageGroupId(transaction.getMerchantId().toString())
                    .messageDeduplicationId(event.getEventId())
                    .build();

            SendMessageResponse response = sqsClient.sendMessage(request);
            
            log.info("Published payment event: eventId={}, transactionId={}, status={}, messageId={}",
                    event.getEventId(),
                    transaction.getId(),
                    transaction.getStatus(),
                    response.messageId());
                    
        } catch (JsonProcessingException e) {
            log.error("Failed to serialize payment event for transaction: {}", transaction.getId(), e);
            throw new RuntimeException("Failed to serialize payment event", e);
        } catch (Exception e) {
            log.error("Failed to publish payment event for transaction: {}", transaction.getId(), e);
            throw new RuntimeException("Failed to publish payment event", e);
        }
    }

    private PaymentEvent buildEvent(Transaction transaction, TransactionStatus previousStatus) {
        String eventType = determineEventType(transaction.getStatus());
        
        return PaymentEvent.builder()
                .eventId(UUID.randomUUID().toString())
                .eventType(eventType)
                .timestamp(OffsetDateTime.now())
                .data(PaymentEvent.PaymentEventData.builder()
                        .transactionId(transaction.getId())
                        .merchantId(transaction.getMerchantId())
                        .amount(transaction.getAmount())
                        .currency(transaction.getCurrency())
                        .previousStatus(previousStatus)
                        .currentStatus(transaction.getStatus())
                        .paymentMethod(transaction.getPaymentMethod())
                        .correlationId(transaction.getCorrelationId())
                        .createdAt(transaction.getCreatedAt())
                        .updatedAt(transaction.getUpdatedAt())
                        .build())
                .build();
    }

    private String determineEventType(TransactionStatus status) {
        return switch (status) {
            case PENDING -> "payment.pending";
            case AUTHORIZED -> "payment.authorized";
            case CAPTURED -> "payment.captured";
            case REFUNDED -> "payment.refunded";
            case CANCELLED -> "payment.cancelled";
            case FAILED -> "payment.failed";
        };
    }
}
