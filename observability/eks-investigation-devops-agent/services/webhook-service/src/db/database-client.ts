/**
 * Database Client for Webhook Service
 * Requirements: 5.3, 5.4
 * 
 * Handles persistence of webhook delivery records and merchant configuration retrieval.
 */

import { Pool, PoolClient } from 'pg';
import { v4 as uuidv4 } from 'uuid';
import {
  WebhookDelivery,
  WebhookDeliveryStatus,
  MerchantWebhookConfig,
  PaymentEventPayload,
  DEFAULT_RETRY_CONFIG
} from '../types';

export class DatabaseClient {
  private pool: Pool | null = null;
  private connectionString: string;

  constructor(connectionString: string) {
    this.connectionString = connectionString;
  }

  /**
   * Initialize the database connection pool
   */
  async connect(): Promise<void> {
    if (this.pool) return;

    this.pool = new Pool({
      connectionString: this.connectionString,
      ssl: process.env.NODE_ENV === 'production' ? { rejectUnauthorized: true } : false,
      max: 10,
      idleTimeoutMillis: 30000,
      connectionTimeoutMillis: 2000
    });

    // Test connection
    const client = await this.pool.connect();
    client.release();
  }

  /**
   * Close the database connection pool
   */
  async disconnect(): Promise<void> {
    if (this.pool) {
      await this.pool.end();
      this.pool = null;
    }
  }

  /**
   * Get a client from the pool
   */
  private async getClient(): Promise<PoolClient> {
    if (!this.pool) {
      await this.connect();
    }
    return this.pool!.connect();
  }

  /**
   * Get merchant webhook configuration
   */
  async getMerchantWebhookConfig(merchantId: string): Promise<MerchantWebhookConfig | null> {
    const client = await this.getClient();
    try {
      const result = await client.query(
        `SELECT id as merchant_id, webhook_url, webhook_secret 
         FROM merchants 
         WHERE id = $1 AND status = 'ACTIVE' AND webhook_url IS NOT NULL`,
        [merchantId]
      );

      if (result.rows.length === 0) {
        return null;
      }

      const row = result.rows[0];
      return {
        merchantId: row.merchant_id,
        webhookUrl: row.webhook_url,
        webhookSecret: row.webhook_secret
      };
    } finally {
      client.release();
    }
  }

  /**
   * Create a new webhook delivery record
   */
  async createWebhookDelivery(
    transactionId: string,
    merchantId: string,
    eventType: string,
    payload: PaymentEventPayload
  ): Promise<WebhookDelivery> {
    const client = await this.getClient();
    try {
      const id = uuidv4();
      const now = new Date();

      const result = await client.query(
        `INSERT INTO webhook_deliveries 
         (id, transaction_id, merchant_id, event_type, payload, attempt_count, status, created_at, next_attempt_at)
         VALUES ($1, $2, $3, $4, $5, 0, 'PENDING', $6, $6)
         RETURNING *`,
        [id, transactionId, merchantId, eventType, JSON.stringify(payload), now]
      );

      return this.mapRowToDelivery(result.rows[0]);
    } finally {
      client.release();
    }
  }

  /**
   * Update webhook delivery after an attempt
   */
  async updateDeliveryAttempt(
    deliveryId: string,
    success: boolean,
    responseCode: number | null,
    responseBody: string | null
  ): Promise<WebhookDelivery | null> {
    const client = await this.getClient();
    try {
      // First get current attempt count
      const current = await client.query(
        'SELECT attempt_count FROM webhook_deliveries WHERE id = $1',
        [deliveryId]
      );

      if (current.rows.length === 0) {
        return null;
      }

      const currentAttemptCount = current.rows[0].attempt_count;
      const newAttemptCount = currentAttemptCount + 1;
      const now = new Date();

      let status: WebhookDeliveryStatus;
      let nextAttemptAt: Date | null = null;

      if (success) {
        status = 'DELIVERED';
      } else if (newAttemptCount >= DEFAULT_RETRY_CONFIG.maxAttempts) {
        status = 'FAILED';
      } else {
        status = 'PENDING';
        // Calculate next attempt time based on exponential backoff
        const delayMs = DEFAULT_RETRY_CONFIG.backoffDelays[newAttemptCount] || 
                        DEFAULT_RETRY_CONFIG.backoffDelays[DEFAULT_RETRY_CONFIG.backoffDelays.length - 1];
        nextAttemptAt = new Date(now.getTime() + delayMs);
      }

      const result = await client.query(
        `UPDATE webhook_deliveries 
         SET attempt_count = $1, 
             last_attempt_at = $2, 
             next_attempt_at = $3, 
             status = $4, 
             response_code = $5, 
             response_body = $6
         WHERE id = $7
         RETURNING *`,
        [newAttemptCount, now, nextAttemptAt, status, responseCode, responseBody, deliveryId]
      );

      return result.rows.length > 0 ? this.mapRowToDelivery(result.rows[0]) : null;
    } finally {
      client.release();
    }
  }

  /**
   * Get pending deliveries that are due for retry
   */
  async getPendingDeliveries(limit: number = 100): Promise<WebhookDelivery[]> {
    const client = await this.getClient();
    try {
      const result = await client.query(
        `SELECT * FROM webhook_deliveries 
         WHERE status = 'PENDING' AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
         ORDER BY next_attempt_at ASC NULLS FIRST
         LIMIT $1`,
        [limit]
      );

      return result.rows.map(row => this.mapRowToDelivery(row));
    } finally {
      client.release();
    }
  }

  /**
   * Get a webhook delivery by ID
   */
  async getDeliveryById(deliveryId: string): Promise<WebhookDelivery | null> {
    const client = await this.getClient();
    try {
      const result = await client.query(
        'SELECT * FROM webhook_deliveries WHERE id = $1',
        [deliveryId]
      );

      return result.rows.length > 0 ? this.mapRowToDelivery(result.rows[0]) : null;
    } finally {
      client.release();
    }
  }

  /**
   * Map database row to WebhookDelivery object
   */
  private mapRowToDelivery(row: Record<string, unknown>): WebhookDelivery {
    return {
      id: row.id as string,
      transactionId: row.transaction_id as string,
      merchantId: row.merchant_id as string,
      eventType: row.event_type as string,
      payload: typeof row.payload === 'string' ? JSON.parse(row.payload) : row.payload as PaymentEventPayload,
      attemptCount: row.attempt_count as number,
      lastAttemptAt: row.last_attempt_at ? new Date(row.last_attempt_at as string) : null,
      nextAttemptAt: row.next_attempt_at ? new Date(row.next_attempt_at as string) : null,
      status: row.status as WebhookDeliveryStatus,
      responseCode: row.response_code as number | null,
      responseBody: row.response_body as string | null,
      createdAt: new Date(row.created_at as string)
    };
  }
}
