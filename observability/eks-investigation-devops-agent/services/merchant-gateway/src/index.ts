/**
 * Merchant Gateway Service - Main Entry Point
 * 
 * API Gateway service that routes requests, handles authentication,
 * and enforces rate limiting for the DevOps Agent EKS Demo.
 * 
 * Requirements: 3.1, 3.2
 */

import express from 'express';
import rateLimit from 'express-rate-limit';
import { correlationMiddleware } from './middleware/correlation';
import { jwtAuthMiddleware } from './middleware/jwt-auth';
import { rateLimitMiddleware } from './middleware/rate-limit';
import { paymentRoutes } from './routes/payments';
import { healthRoutes } from './routes/health';

const app = express();
const PORT = process.env.PORT || 3000;

// Parse JSON bodies
app.use(express.json());

// Apply global rate limiting to all routes to prevent brute-force/DDoS
const globalRateLimit = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 1000, // generous global limit
  standardHeaders: true,
  legacyHeaders: false,
  message: {
    error: {
      code: 'RATE_LIMIT_EXCEEDED',
      message: 'Too many requests from this IP, please try again later.',
      timestamp: new Date().toISOString(),
    },
  },
});
app.use(globalRateLimit);

// Health check routes (no auth required)
app.use('/health', healthRoutes);

// Apply correlation ID middleware to all requests
app.use(correlationMiddleware);

// Protected API routes
app.use('/api/v1', jwtAuthMiddleware);
app.use('/api/v1', rateLimitMiddleware);
app.use('/api/v1/payments', paymentRoutes);

// Error handling middleware
app.use((err: Error, req: express.Request, res: express.Response, _next: express.NextFunction) => {
  const correlationId = req.headers['x-correlation-id'] || 'unknown';
  console.error(JSON.stringify({
    level: 'error',
    correlationId,
    message: err.message,
    stack: err.stack,
    timestamp: new Date().toISOString(),
  }));

  res.status(500).json({
    error: {
      code: 'INTERNAL_ERROR',
      message: 'An unexpected error occurred',
      correlationId,
      timestamp: new Date().toISOString(),
    },
  });
});

// 404 handler
app.use((req: express.Request, res: express.Response) => {
  const correlationId = req.headers['x-correlation-id'] || 'unknown';
  res.status(404).json({
    error: {
      code: 'NOT_FOUND',
      message: `Route ${req.method} ${req.path} not found`,
      correlationId,
      timestamp: new Date().toISOString(),
    },
  });
});

// Start server only if not in test mode
if (process.env.NODE_ENV !== 'test') {
  app.listen(PORT, () => {
    console.log(JSON.stringify({
      level: 'info',
      message: `Merchant Gateway started on port ${PORT}`,
      timestamp: new Date().toISOString(),
    }));
  });
}

export { app };
