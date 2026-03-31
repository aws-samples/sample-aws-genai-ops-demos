/**
 * Webhook Service Types
 * Requirements: 5.1, 5.2, 5.3, 5.4, 5.5
 */

/**
 * Payment event received from SQS
 */
export interface PaymentEvent {
  transactionId: string;
  merchantId: string;
  eventType: PaymentEventType;
  payload: PaymentEventPayload;
  timestamp: string;
}

/**
 * Types of payment events
 */
export type PaymentEventType = 
  | 'PAYMENT_AUTHORIZED'
  | 'PAYMENT_CAPTURED'
  | 'PAYMENT_REFUNDED'
  | 'PAYMENT_CANCELED'
  | 'PAYMENT_FAILED';

/**
 * Payment event payload details
 */
export interface PaymentEventPayload {
  transactionId: string;
  merchantId: string;
  amount: number;
  currency: string;
  status: string;
  authorizationCode?: string;
  captureId?: string;
  refundId?: string;
  errorCode?: string;
  errorMessage?: string;
  metadata?: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

/**
 * Merchant configuration for webhook delivery
 */
export interface MerchantWebhookConfig {
  merchantId: string;
  webhookUrl: string;
  webhookSecret: string;
}

/**
 * Webhook delivery status
 */
export type WebhookDeliveryStatus = 'PENDING' | 'DELIVERED' | 'FAILED';

/**
 * Webhook delivery record stored in database
 */
export interface WebhookDelivery {
  id: string;
  transactionId: string;
  merchantId: string;
  eventType: string;
  payload: PaymentEventPayload;
  attemptCount: number;
  lastAttemptAt: Date | null;
  nextAttemptAt: Date | null;
  status: WebhookDeliveryStatus;
  responseCode: number | null;
  responseBody: string | null;
  createdAt: Date;
}

/**
 * Result of a webhook delivery attempt
 */
export interface DeliveryResult {
  success: boolean;
  statusCode?: number;
  responseBody?: string;
  error?: string;
}

/**
 * Retry configuration
 */
export interface RetryConfig {
  maxAttempts: number;
  backoffDelays: number[]; // delays in milliseconds
}

/**
 * Default retry configuration
 * Attempt 1: Immediate
 * Attempt 2: 1 minute delay
 * Attempt 3: 5 minutes delay
 * Attempt 4: 30 minutes delay
 * Attempt 5: 2 hours delay
 */
export const DEFAULT_RETRY_CONFIG: RetryConfig = {
  maxAttempts: 5,
  backoffDelays: [
    0,           // Attempt 1: Immediate
    60000,       // Attempt 2: 1 minute
    300000,      // Attempt 3: 5 minutes
    1800000,     // Attempt 4: 30 minutes
    7200000      // Attempt 5: 2 hours
  ]
};
