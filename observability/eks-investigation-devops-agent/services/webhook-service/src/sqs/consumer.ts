/**
 * SQS Message Consumer
 * Requirements: 5.1
 * 
 * Polls payment events from SQS queue and triggers webhook delivery.
 */

import {
  SQSClient,
  ReceiveMessageCommand,
  DeleteMessageCommand,
  Message
} from '@aws-sdk/client-sqs';
import { PaymentEvent } from '../types';
import { WebhookDeliveryService } from '../delivery/webhook-delivery';

export class SQSConsumer {
  private sqsClient: SQSClient;
  private queueUrl: string;
  private webhookDeliveryService: WebhookDeliveryService;
  private isRunning: boolean = false;
  private pollIntervalMs: number = 1000;
  private maxMessages: number = 10;
  private waitTimeSeconds: number = 20; // Long polling

  constructor(queueUrl: string, webhookDeliveryService: WebhookDeliveryService) {
    this.queueUrl = queueUrl;
    this.webhookDeliveryService = webhookDeliveryService;
    this.sqsClient = new SQSClient({
      region: process.env.AWS_REGION || 'us-east-1'
    });
  }

  /**
   * Start consuming messages from the queue
   */
  async start(): Promise<void> {
    if (!this.queueUrl) {
      console.log('SQS queue URL not configured, skipping message polling');
      return;
    }

    if (this.isRunning) {
      console.log('SQS Consumer is already running');
      return;
    }

    this.isRunning = true;
    console.log(`Starting SQS Consumer for queue: ${this.queueUrl}`);

    while (this.isRunning) {
      try {
        await this.pollMessages();
      } catch (error) {
        console.error('Error polling messages:', error);
        // Wait before retrying on error
        await this.sleep(this.pollIntervalMs);
      }
    }
  }

  /**
   * Stop consuming messages
   */
  async stop(): Promise<void> {
    console.log('Stopping SQS Consumer...');
    this.isRunning = false;
  }

  /**
   * Poll messages from the queue
   */
  private async pollMessages(): Promise<void> {
    const command = new ReceiveMessageCommand({
      QueueUrl: this.queueUrl,
      MaxNumberOfMessages: this.maxMessages,
      WaitTimeSeconds: this.waitTimeSeconds,
      MessageAttributeNames: ['All']
    });

    const response = await this.sqsClient.send(command);

    if (response.Messages && response.Messages.length > 0) {
      console.log(`Received ${response.Messages.length} messages`);
      
      for (const message of response.Messages) {
        await this.processMessage(message);
      }
    }
  }

  /**
   * Process a single message
   */
  private async processMessage(message: Message): Promise<void> {
    if (!message.Body || !message.ReceiptHandle) {
      console.warn('Received message without body or receipt handle');
      return;
    }

    try {
      const paymentEvent = this.parsePaymentEvent(message.Body);
      
      if (!paymentEvent) {
        console.error('Failed to parse payment event, deleting message');
        await this.deleteMessage(message.ReceiptHandle);
        return;
      }

      console.log(`Processing payment event: ${paymentEvent.eventType} for transaction ${paymentEvent.transactionId}`);

      // Queue the webhook delivery
      await this.webhookDeliveryService.queueDelivery(paymentEvent);

      // Delete the message from the queue after successful processing
      await this.deleteMessage(message.ReceiptHandle);
      
      console.log(`Successfully processed event for transaction ${paymentEvent.transactionId}`);
    } catch (error) {
      console.error('Error processing message:', error);
      // Don't delete the message - it will be retried via SQS visibility timeout
    }
  }

  /**
   * Parse the message body into a PaymentEvent
   */
  parsePaymentEvent(messageBody: string): PaymentEvent | null {
    try {
      const parsed = JSON.parse(messageBody);
      
      // Validate required fields
      if (!parsed.transactionId || !parsed.merchantId || !parsed.eventType || !parsed.payload) {
        console.error('Payment event missing required fields');
        return null;
      }

      return {
        transactionId: parsed.transactionId,
        merchantId: parsed.merchantId,
        eventType: parsed.eventType,
        payload: parsed.payload,
        timestamp: parsed.timestamp || new Date().toISOString()
      };
    } catch (error) {
      console.error('Failed to parse message body as JSON:', error);
      return null;
    }
  }

  /**
   * Delete a message from the queue
   */
  private async deleteMessage(receiptHandle: string): Promise<void> {
    const command = new DeleteMessageCommand({
      QueueUrl: this.queueUrl,
      ReceiptHandle: receiptHandle
    });

    await this.sqsClient.send(command);
  }

  /**
   * Sleep for a specified duration
   */
  private sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}
