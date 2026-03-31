/**
 * Webhook Service Entry Point
 * Requirements: 5.1, 5.2
 * 
 * This service consumes payment events from SQS and delivers webhooks
 * to merchant endpoints with retry logic and HMAC-SHA256 signatures.
 */

import http from 'http';
import { SQSConsumer } from './sqs/consumer';
import { WebhookDeliveryService } from './delivery/webhook-delivery';
import { SignatureService } from './signature/signature-service';
import { DatabaseClient } from './db/database-client';

const PORT = process.env.PORT || 3001;
const SQS_QUEUE_URL = process.env.SQS_QUEUE_URL || '';
const DATABASE_URL = process.env.DATABASE_URL || '';

let sqsConsumer: SQSConsumer | null = null;
let isShuttingDown = false;

/**
 * Create a simple health check server
 */
function createHealthServer(): http.Server {
  return http.createServer((req, res) => {
    if (req.url === '/health' && req.method === 'GET') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'healthy', service: 'webhook-service' }));
    } else {
      res.writeHead(404);
      res.end();
    }
  });
}

/**
 * Graceful shutdown handler
 */
async function shutdown(signal: string): Promise<void> {
  if (isShuttingDown) return;
  isShuttingDown = true;

  console.log(`Received ${signal}, starting graceful shutdown...`);

  if (sqsConsumer) {
    await sqsConsumer.stop();
  }

  console.log('Shutdown complete');
  process.exit(0);
}

/**
 * Main application entry point
 */
async function main(): Promise<void> {
  console.log('Starting Webhook Service...');

  // Initialize services
  const signatureService = new SignatureService();
  const databaseClient = new DatabaseClient(DATABASE_URL);
  const webhookDeliveryService = new WebhookDeliveryService(
    signatureService,
    databaseClient
  );

  // Initialize SQS consumer
  sqsConsumer = new SQSConsumer(SQS_QUEUE_URL, webhookDeliveryService);

  // Start health check server
  const healthServer = createHealthServer();
  healthServer.listen(PORT, () => {
    console.log(`Health check server listening on port ${PORT}`);
  });

  // Start consuming messages (only if SQS queue URL is configured)
  if (SQS_QUEUE_URL) {
    await sqsConsumer.start();
  } else {
    console.log('SQS_QUEUE_URL not set, webhook service running in health-check-only mode');
  }

  // Setup graceful shutdown
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));

  console.log('Webhook Service started successfully');
}

// Run the application
main().catch((error) => {
  console.error('Failed to start Webhook Service:', error);
  process.exit(1);
});

export { main };
