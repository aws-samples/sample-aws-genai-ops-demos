/**
 * Health Check Routes
 * 
 * Provides health check endpoints for Kubernetes probes.
 */

import { Router, Request, Response } from 'express';

const router = Router();

/**
 * Liveness probe - checks if the service is running
 */
router.get('/', (_req: Request, res: Response) => {
  res.status(200).json({
    status: 'healthy',
    timestamp: new Date().toISOString(),
  });
});

/**
 * Readiness probe - checks if the service is ready to accept traffic
 */
router.get('/ready', (_req: Request, res: Response) => {
  // In production, this could check database connections, etc.
  res.status(200).json({
    status: 'ready',
    timestamp: new Date().toISOString(),
  });
});

export { router as healthRoutes };
