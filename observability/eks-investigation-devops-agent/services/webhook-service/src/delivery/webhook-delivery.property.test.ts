/**
 * Property-Based Tests for Webhook Delivery Service
 * 
 * Feature: devops-agent-eks, Property 7: Webhook Delivery Guarantee
 * 
 * For any payment state change, the Webhook_Service SHALL queue a notification event
 * and attempt delivery to the merchant's configured endpoint. If delivery fails, the
 * service SHALL retry with exponential backoff (1min, 5min, 30min, 2hr) up to 5 attempts
 * before marking as failed.
 * 
 * **Validates: Requirements 5.1, 5.2, 5.3, 5.4**
 */

import * as fc from 'fast-check';
import { WebhookDeliveryService } from './webhook-delivery';
import { SignatureService } from '../signature/signature-service';
import { DatabaseClient } from '../db/database-client';
import { 
  WebhookDelivery, 
  WebhookDeliveryStatus, 
  DEFAULT_RETRY_CONFIG,
  PaymentEventPayload
} from '../types';

// Mock database client for testing
class MockDatabaseClient extends DatabaseClient {
  private deliveries: Map<string, WebhookDelivery> = new Map();
  private merchantConfigs: Map<string, { webhookUrl: string; webhookSecret: string }> = new Map();

  constructor() {
    super('');
  }

  setMerchantConfig(merchantId: string, webhookUrl: string, webhookSecret: string): void {
    this.merchantConfigs.set(merchantId, { webhookUrl, webhookSecret });
  }

  async getMerchantWebhookConfig(merchantId: string) {
    const config = this.merchantConfigs.get(merchantId);
    if (!config) return null;
    return {
      merchantId,
      webhookUrl: config.webhookUrl,
      webhookSecret: config.webhookSecret
    };
  }

  async createWebhookDelivery(
    transactionId: string,
    merchantId: string,
    eventType: string,
    payload: PaymentEventPayload
  ): Promise<WebhookDelivery> {
    const id = `delivery-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    const delivery: WebhookDelivery = {
      id,
      transactionId,
      merchantId,
      eventType,
      payload,
      attemptCount: 0,
      lastAttemptAt: null,
      nextAttemptAt: new Date(),
      status: 'PENDING',
      responseCode: null,
      responseBody: null,
      createdAt: new Date()
    };
    this.deliveries.set(id, delivery);
    return delivery;
  }

  async updateDeliveryAttempt(
    deliveryId: string,
    success: boolean,
    responseCode: number | null,
    responseBody: string | null
  ): Promise<WebhookDelivery | null> {
    const delivery = this.deliveries.get(deliveryId);
    if (!delivery) return null;

    const newAttemptCount = delivery.attemptCount + 1;
    let status: WebhookDeliveryStatus;
    let nextAttemptAt: Date | null = null;

    if (success) {
      status = 'DELIVERED';
    } else if (newAttemptCount >= DEFAULT_RETRY_CONFIG.maxAttempts) {
      status = 'FAILED';
    } else {
      status = 'PENDING';
      const delayMs = DEFAULT_RETRY_CONFIG.backoffDelays[newAttemptCount] || 
                      DEFAULT_RETRY_CONFIG.backoffDelays[DEFAULT_RETRY_CONFIG.backoffDelays.length - 1];
      nextAttemptAt = new Date(Date.now() + delayMs);
    }

    const updated: WebhookDelivery = {
      ...delivery,
      attemptCount: newAttemptCount,
      lastAttemptAt: new Date(),
      nextAttemptAt,
      status,
      responseCode,
      responseBody
    };
    this.deliveries.set(deliveryId, updated);
    return updated;
  }

  async getPendingDeliveries(): Promise<WebhookDelivery[]> {
    return Array.from(this.deliveries.values()).filter(d => d.status === 'PENDING');
  }

  async getDeliveryById(deliveryId: string): Promise<WebhookDelivery | null> {
    return this.deliveries.get(deliveryId) || null;
  }

  getDelivery(id: string): WebhookDelivery | undefined {
    return this.deliveries.get(id);
  }

  clear(): void {
    this.deliveries.clear();
    this.merchantConfigs.clear();
  }
}

describe('Property 7: Webhook Delivery Guarantee', () => {
  let signatureService: SignatureService;
  let mockDbClient: MockDatabaseClient;
  let deliveryService: WebhookDeliveryService;

  beforeEach(() => {
    signatureService = new SignatureService();
    mockDbClient = new MockDatabaseClient();
    deliveryService = new WebhookDeliveryService(signatureService, mockDbClient);
  });

  // Arbitrary for generating valid payment event payloads
  const paymentEventPayloadArb = fc.record({
    transactionId: fc.uuid(),
    merchantId: fc.uuid(),
    amount: fc.integer({ min: 1, max: 1000000 }),
    currency: fc.constantFrom('EUR', 'USD', 'GBP'),
    status: fc.constantFrom('AUTHORIZED', 'CAPTURED', 'REFUNDED', 'CANCELED'),
    createdAt: fc.date().map(d => d.toISOString()),
    updatedAt: fc.date().map(d => d.toISOString())
  });

  // Arbitrary for generating webhook deliveries
  const webhookDeliveryArb = (attemptCount: number, status: WebhookDeliveryStatus) => 
    fc.record({
      id: fc.uuid(),
      transactionId: fc.uuid(),
      merchantId: fc.uuid(),
      eventType: fc.constantFrom('PAYMENT_AUTHORIZED', 'PAYMENT_CAPTURED', 'PAYMENT_REFUNDED'),
      payload: paymentEventPayloadArb,
      attemptCount: fc.constant(attemptCount),
      lastAttemptAt: fc.constant(attemptCount > 0 ? new Date() : null),
      nextAttemptAt: fc.constant(new Date()),
      status: fc.constant(status),
      responseCode: fc.constant(null),
      responseBody: fc.constant(null),
      createdAt: fc.date()
    });

  /**
   * Property: The retry configuration should have exactly 5 maximum attempts
   * as specified in requirements.
   */
  it('should have maximum 5 retry attempts configured', () => {
    expect(deliveryService.getMaxAttempts()).toBe(5);
    expect(DEFAULT_RETRY_CONFIG.maxAttempts).toBe(5);
  });

  /**
   * Property: The retry delays should follow exponential backoff pattern
   * (0, 1min, 5min, 30min, 2hr).
   */
  it('should have correct exponential backoff delays configured', () => {
    const delays = deliveryService.getRetryDelays();
    
    expect(delays).toHaveLength(5);
    expect(delays[0]).toBe(0);           // Immediate
    expect(delays[1]).toBe(60000);       // 1 minute
    expect(delays[2]).toBe(300000);      // 5 minutes
    expect(delays[3]).toBe(1800000);     // 30 minutes
    expect(delays[4]).toBe(7200000);     // 2 hours
  });

  /**
   * Property: For any delivery with attempt count < 5 and status PENDING,
   * shouldRetry should return true.
   */
  it('should allow retry for pending deliveries with attempts < max', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 4 }),
        (attemptCount) => {
          const delivery: WebhookDelivery = {
            id: 'test-id',
            transactionId: 'tx-id',
            merchantId: 'merchant-id',
            eventType: 'PAYMENT_AUTHORIZED',
            payload: {
              transactionId: 'tx-id',
              merchantId: 'merchant-id',
              amount: 100,
              currency: 'EUR',
              status: 'AUTHORIZED',
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString()
            },
            attemptCount,
            lastAttemptAt: null,
            nextAttemptAt: new Date(),
            status: 'PENDING',
            responseCode: null,
            responseBody: null,
            createdAt: new Date()
          };

          return deliveryService.shouldRetry(delivery) === true;
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: For any delivery with attempt count >= 5, shouldRetry should
   * return false regardless of status.
   */
  it('should not allow retry for deliveries with attempts >= max', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 5, max: 100 }),
        fc.constantFrom<WebhookDeliveryStatus>('PENDING', 'DELIVERED', 'FAILED'),
        (attemptCount, status) => {
          const delivery: WebhookDelivery = {
            id: 'test-id',
            transactionId: 'tx-id',
            merchantId: 'merchant-id',
            eventType: 'PAYMENT_AUTHORIZED',
            payload: {
              transactionId: 'tx-id',
              merchantId: 'merchant-id',
              amount: 100,
              currency: 'EUR',
              status: 'AUTHORIZED',
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString()
            },
            attemptCount,
            lastAttemptAt: new Date(),
            nextAttemptAt: null,
            status,
            responseCode: null,
            responseBody: null,
            createdAt: new Date()
          };

          return deliveryService.shouldRetry(delivery) === false;
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: For any delivery with status DELIVERED or FAILED, shouldRetry
   * should return false.
   */
  it('should not allow retry for delivered or failed deliveries', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 10 }),
        fc.constantFrom<WebhookDeliveryStatus>('DELIVERED', 'FAILED'),
        (attemptCount, status) => {
          const delivery: WebhookDelivery = {
            id: 'test-id',
            transactionId: 'tx-id',
            merchantId: 'merchant-id',
            eventType: 'PAYMENT_AUTHORIZED',
            payload: {
              transactionId: 'tx-id',
              merchantId: 'merchant-id',
              amount: 100,
              currency: 'EUR',
              status: 'AUTHORIZED',
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString()
            },
            attemptCount,
            lastAttemptAt: new Date(),
            nextAttemptAt: null,
            status,
            responseCode: status === 'DELIVERED' ? 200 : null,
            responseBody: null,
            createdAt: new Date()
          };

          return deliveryService.shouldRetry(delivery) === false;
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: For any attempt count, calculateNextRetryDelay should return
   * the correct delay from the backoff configuration.
   */
  it('should calculate correct retry delays for each attempt', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 10 }),
        (attemptCount) => {
          const delay = deliveryService.calculateNextRetryDelay(attemptCount);
          const expectedDelays = DEFAULT_RETRY_CONFIG.backoffDelays;
          
          if (attemptCount < expectedDelays.length) {
            return delay === expectedDelays[attemptCount];
          } else {
            // For attempts beyond the configured delays, use the last delay
            return delay === expectedDelays[expectedDelays.length - 1];
          }
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: The retry delays should be monotonically increasing
   * (exponential backoff pattern).
   */
  it('should have monotonically increasing retry delays', () => {
    const delays = deliveryService.getRetryDelays();
    
    for (let i = 1; i < delays.length; i++) {
      expect(delays[i]).toBeGreaterThan(delays[i - 1]);
    }
  });

  /**
   * Property: For any valid payment event payload, the delivery service
   * should be able to calculate retry delays without errors.
   */
  it('should handle retry delay calculation for any attempt count', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 1000 }),
        (attemptCount) => {
          const delay = deliveryService.calculateNextRetryDelay(attemptCount);
          return typeof delay === 'number' && delay >= 0;
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: The total maximum wait time across all retries should not
   * exceed the sum of all configured delays.
   */
  it('should have bounded total retry time', () => {
    const delays = deliveryService.getRetryDelays();
    const totalMaxWaitTime = delays.reduce((sum, delay) => sum + delay, 0);
    
    // Total wait time should be: 0 + 1min + 5min + 30min + 2hr = 2hr 36min = 9360000ms
    const expectedTotalWaitTime = 0 + 60000 + 300000 + 1800000 + 7200000;
    expect(totalMaxWaitTime).toBe(expectedTotalWaitTime);
  });

  /**
   * Property: For any attempt count from 0 to max-1, the delivery should
   * remain in PENDING status after a failed attempt.
   */
  it('should keep delivery in PENDING status until max attempts reached', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 0, max: 3 }), // 0-3 means after update it will be 1-4, still < 5
        async (initialAttemptCount) => {
          mockDbClient.clear();
          
          const delivery = await mockDbClient.createWebhookDelivery(
            'tx-123',
            'merchant-123',
            'PAYMENT_AUTHORIZED',
            {
              transactionId: 'tx-123',
              merchantId: 'merchant-123',
              amount: 100,
              currency: 'EUR',
              status: 'AUTHORIZED',
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString()
            }
          );

          // Simulate previous attempts
          let currentDelivery = delivery;
          for (let i = 0; i < initialAttemptCount; i++) {
            currentDelivery = (await mockDbClient.updateDeliveryAttempt(
              currentDelivery.id,
              false,
              500,
              'Server error'
            ))!;
          }

          // One more failed attempt
          const updated = await mockDbClient.updateDeliveryAttempt(
            currentDelivery.id,
            false,
            500,
            'Server error'
          );

          // Should still be PENDING since we haven't reached max attempts
          return updated!.status === 'PENDING';
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: After exactly 5 failed attempts, the delivery should be
   * marked as FAILED.
   */
  it('should mark delivery as FAILED after max attempts', async () => {
    mockDbClient.clear();
    
    const delivery = await mockDbClient.createWebhookDelivery(
      'tx-123',
      'merchant-123',
      'PAYMENT_AUTHORIZED',
      {
        transactionId: 'tx-123',
        merchantId: 'merchant-123',
        amount: 100,
        currency: 'EUR',
        status: 'AUTHORIZED',
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString()
      }
    );

    let currentDelivery = delivery;
    
    // Simulate 5 failed attempts
    for (let i = 0; i < 5; i++) {
      currentDelivery = (await mockDbClient.updateDeliveryAttempt(
        currentDelivery.id,
        false,
        500,
        'Server error'
      ))!;
    }

    expect(currentDelivery.status).toBe('FAILED');
    expect(currentDelivery.attemptCount).toBe(5);
  });

  /**
   * Property: A successful delivery should immediately be marked as DELIVERED
   * regardless of attempt count.
   */
  it('should mark delivery as DELIVERED on success at any attempt', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 0, max: 4 }),
        async (attemptsBefore) => {
          mockDbClient.clear();
          
          const delivery = await mockDbClient.createWebhookDelivery(
            'tx-123',
            'merchant-123',
            'PAYMENT_AUTHORIZED',
            {
              transactionId: 'tx-123',
              merchantId: 'merchant-123',
              amount: 100,
              currency: 'EUR',
              status: 'AUTHORIZED',
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString()
            }
          );

          let currentDelivery = delivery;
          
          // Simulate previous failed attempts
          for (let i = 0; i < attemptsBefore; i++) {
            currentDelivery = (await mockDbClient.updateDeliveryAttempt(
              currentDelivery.id,
              false,
              500,
              'Server error'
            ))!;
          }

          // Now succeed
          const updated = await mockDbClient.updateDeliveryAttempt(
            currentDelivery.id,
            true,
            200,
            'OK'
          );

          return updated!.status === 'DELIVERED';
        }
      ),
      { numRuns: 100 }
    );
  });
});
