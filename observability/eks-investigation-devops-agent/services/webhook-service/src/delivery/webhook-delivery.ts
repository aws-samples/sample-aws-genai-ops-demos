/**
 * Webhook Delivery Service
 * Requirements: 5.2, 5.3, 5.4, 5.5
 * 
 * Property 7: Webhook Delivery Guarantee
 * For any payment state change, the Webhook_Service SHALL queue a notification event
 * and attempt delivery to the merchant's configured endpoint. If delivery fails, the
 * service SHALL retry with exponential backoff (1min, 5min, 30min, 2hr) up to 5 attempts
 * before marking as failed.
 */

import axios, { AxiosError } from 'axios';
import { SignatureService } from '../signature/signature-service';
import { DatabaseClient } from '../db/database-client';
import {
  PaymentEvent,
  DeliveryResult,
  WebhookDelivery,
  DEFAULT_RETRY_CONFIG
} from '../types';

export class WebhookDeliveryService {
  private signatureService: SignatureService;
  private databaseClient: DatabaseClient;
  private deliveryTimeoutMs: number = 30000; // 30 seconds

  constructor(signatureService: SignatureService, databaseClient: DatabaseClient) {
    this.signatureService = signatureService;
    this.databaseClient = databaseClient;
  }

  /**
   * Queue a webhook delivery for a payment event
   * Creates a delivery record and attempts immediate delivery
   */
  async queueDelivery(event: PaymentEvent): Promise<void> {
    // Get merchant webhook configuration
    const merchantConfig = await this.databaseClient.getMerchantWebhookConfig(event.merchantId);

    if (!merchantConfig || !merchantConfig.webhookUrl) {
      console.log(`No webhook configured for merchant ${event.merchantId}, skipping delivery`);
      return;
    }

    // Create delivery record
    const delivery = await this.databaseClient.createWebhookDelivery(
      event.transactionId,
      event.merchantId,
      event.eventType,
      event.payload
    );

    console.log(`Created webhook delivery ${delivery.id} for transaction ${event.transactionId}`);

    // Attempt immediate delivery
    await this.attemptDelivery(delivery, merchantConfig.webhookUrl, merchantConfig.webhookSecret);
  }

  /**
   * Attempt to deliver a webhook
   */
  async attemptDelivery(
    delivery: WebhookDelivery,
    webhookUrl: string,
    webhookSecret: string
  ): Promise<DeliveryResult> {
    const payload = {
      event: delivery.eventType,
      data: delivery.payload,
      deliveryId: delivery.id,
      timestamp: new Date().toISOString()
    };

    const payloadString = JSON.stringify(payload);
    const signature = this.signatureService.generateSignature(payloadString, webhookSecret);
    const signatureHeader = this.signatureService.formatSignatureHeader(signature);

    console.log(`Attempting delivery ${delivery.id} (attempt ${delivery.attemptCount + 1}/${DEFAULT_RETRY_CONFIG.maxAttempts})`);

    try {
      const response = await axios.post(webhookUrl, payload, {
        headers: {
          'Content-Type': 'application/json',
          'X-Webhook-Signature': signatureHeader,
          'X-Delivery-Id': delivery.id,
          'X-Event-Type': delivery.eventType
        },
        timeout: this.deliveryTimeoutMs,
        validateStatus: () => true // Don't throw on non-2xx status
      });

      const success = response.status >= 200 && response.status < 300;
      const responseBody = typeof response.data === 'string' 
        ? response.data 
        : JSON.stringify(response.data);

      // Update delivery record
      await this.databaseClient.updateDeliveryAttempt(
        delivery.id,
        success,
        response.status,
        responseBody.substring(0, 1000) // Limit response body size
      );

      if (success) {
        console.log(`Delivery ${delivery.id} succeeded with status ${response.status}`);
      } else {
        console.log(`Delivery ${delivery.id} failed with status ${response.status}`);
      }

      return {
        success,
        statusCode: response.status,
        responseBody
      };
    } catch (error) {
      const axiosError = error as AxiosError;
      const errorMessage = axiosError.message || 'Unknown error';

      console.error(`Delivery ${delivery.id} failed with error: ${errorMessage}`);

      // Update delivery record with failure
      await this.databaseClient.updateDeliveryAttempt(
        delivery.id,
        false,
        null,
        errorMessage.substring(0, 1000)
      );

      return {
        success: false,
        error: errorMessage
      };
    }
  }

  /**
   * Process pending deliveries that are due for retry
   * This should be called periodically by a scheduler
   */
  async processPendingDeliveries(): Promise<number> {
    const pendingDeliveries = await this.databaseClient.getPendingDeliveries(100);
    let processedCount = 0;

    for (const delivery of pendingDeliveries) {
      const merchantConfig = await this.databaseClient.getMerchantWebhookConfig(delivery.merchantId);

      if (!merchantConfig || !merchantConfig.webhookUrl) {
        console.log(`Merchant ${delivery.merchantId} no longer has webhook configured, marking as failed`);
        await this.databaseClient.updateDeliveryAttempt(
          delivery.id,
          false,
          null,
          'Merchant webhook not configured'
        );
        continue;
      }

      await this.attemptDelivery(delivery, merchantConfig.webhookUrl, merchantConfig.webhookSecret);
      processedCount++;
    }

    return processedCount;
  }

  /**
   * Calculate the next retry delay based on attempt count
   */
  calculateNextRetryDelay(attemptCount: number): number {
    if (attemptCount >= DEFAULT_RETRY_CONFIG.backoffDelays.length) {
      return DEFAULT_RETRY_CONFIG.backoffDelays[DEFAULT_RETRY_CONFIG.backoffDelays.length - 1];
    }
    return DEFAULT_RETRY_CONFIG.backoffDelays[attemptCount];
  }

  /**
   * Check if a delivery should be retried
   */
  shouldRetry(delivery: WebhookDelivery): boolean {
    return delivery.status === 'PENDING' && 
           delivery.attemptCount < DEFAULT_RETRY_CONFIG.maxAttempts;
  }

  /**
   * Get the maximum number of retry attempts
   */
  getMaxAttempts(): number {
    return DEFAULT_RETRY_CONFIG.maxAttempts;
  }

  /**
   * Get the retry delays configuration
   */
  getRetryDelays(): number[] {
    return [...DEFAULT_RETRY_CONFIG.backoffDelays];
  }
}
