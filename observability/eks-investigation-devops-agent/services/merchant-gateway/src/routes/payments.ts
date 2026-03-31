/**
 * Payment Routes
 * 
 * API routes for payment operations that forward requests to the payment processor.
 * 
 * Requirements: 3.2
 */

import { Router, Request, Response } from 'express';
import { createCorrelatedLogger } from '../middleware/correlation';

const router = Router();

// Payment processor service URL (includes /api/v1 prefix)
const PAYMENT_PROCESSOR_URL = process.env.PAYMENT_PROCESSOR_URL || 'http://payment-processor:8080';
const PAYMENT_API_PREFIX = '/api/v1';

/**
 * Forwards request to payment processor
 */
async function forwardToPaymentProcessor(
  req: Request,
  res: Response,
  path: string,
  method: 'GET' | 'POST'
): Promise<void> {
  const correlationId = req.correlationId || 'unknown';
  const logger = createCorrelatedLogger(correlationId);
  
  const url = `${PAYMENT_PROCESSOR_URL}${PAYMENT_API_PREFIX}${path}`;
  
  logger.info('Forwarding request to payment processor', {
    method,
    path,
    merchantId: req.merchant?.merchantId,
  });

  try {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Correlation-ID': correlationId,
      'X-Merchant-ID': req.merchant?.merchantId || '',
    };

    const fetchOptions: RequestInit = {
      method,
      headers,
    };

    if (method === 'POST' && req.body) {
      fetchOptions.body = JSON.stringify(req.body);
    }

    const response = await fetch(url, fetchOptions);
    const data = await response.json();

    logger.info('Payment processor response', {
      status: response.status,
      path,
    });

    res.status(response.status).json(data);
  } catch (error) {
    const err = error as Error;
    logger.error('Failed to forward request to payment processor', {
      error: err.message,
      path,
    });

    res.status(503).json({
      error: {
        code: 'SERVICE_UNAVAILABLE',
        message: 'Payment processor is unavailable',
        correlationId,
        timestamp: new Date().toISOString(),
      },
    });
  }
}

/**
 * POST /api/v1/payments/authorize
 * Create and authorize a new payment
 */
router.post('/authorize', async (req: Request, res: Response) => {
  await forwardToPaymentProcessor(req, res, '/payments/authorize', 'POST');
});

/**
 * POST /api/v1/payments/:id/capture
 * Capture an authorized payment
 */
router.post('/:id/capture', async (req: Request, res: Response) => {
  await forwardToPaymentProcessor(req, res, `/payments/${req.params.id}/capture`, 'POST');
});

/**
 * POST /api/v1/payments/:id/refund
 * Refund a captured payment
 */
router.post('/:id/refund', async (req: Request, res: Response) => {
  await forwardToPaymentProcessor(req, res, `/payments/${req.params.id}/refund`, 'POST');
});

/**
 * GET /api/v1/payments/:id
 * Get payment status by ID
 */
router.get('/:id', async (req: Request, res: Response) => {
  await forwardToPaymentProcessor(req, res, `/payments/${req.params.id}`, 'GET');
});

/**
 * GET /api/v1/payments
 * List payments with optional filters
 */
router.get('/', async (req: Request, res: Response) => {
  const queryString = new URLSearchParams(req.query as Record<string, string>).toString();
  const path = queryString ? `/payments?${queryString}` : '/payments';
  await forwardToPaymentProcessor(req, res, path, 'GET');
});

export { router as paymentRoutes };
