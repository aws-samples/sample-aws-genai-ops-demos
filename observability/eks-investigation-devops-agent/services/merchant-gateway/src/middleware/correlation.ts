/**
 * Correlation ID Middleware
 * 
 * Generates a unique correlation ID for each request and propagates it
 * through the request lifecycle for distributed tracing.
 * 
 * Requirements: 3.4
 */

import { Request, Response, NextFunction } from 'express';
import { v4 as uuidv4 } from 'uuid';

export const CORRELATION_HEADER = 'x-correlation-id';

declare global {
  namespace Express {
    interface Request {
      correlationId: string;
    }
  }
}

/**
 * Middleware that generates or extracts correlation ID for request tracing
 * Note: Custom correlation IDs are preserved as-is (including whitespace)
 */
export function correlationMiddleware(
  req: Request,
  res: Response,
  next: NextFunction
): void {
  // Use existing correlation ID from header (preserve as-is) or generate new one
  const headerValue = req.headers[CORRELATION_HEADER] as string | undefined;
  const correlationId = headerValue !== undefined && headerValue !== '' 
    ? headerValue 
    : uuidv4();
  
  // Attach to request object
  req.correlationId = correlationId;
  
  // Add to response headers
  res.setHeader(CORRELATION_HEADER, correlationId);
  
  // Log the request with correlation ID
  console.log(JSON.stringify({
    level: 'info',
    correlationId,
    method: req.method,
    path: req.path,
    timestamp: new Date().toISOString(),
  }));
  
  next();
}

/**
 * Creates a logger function that includes correlation ID
 */
export function createCorrelatedLogger(correlationId: string) {
  return {
    info: (message: string, data?: Record<string, unknown>) => {
      console.log(JSON.stringify({
        level: 'info',
        correlationId,
        message,
        ...data,
        timestamp: new Date().toISOString(),
      }));
    },
    error: (message: string, data?: Record<string, unknown>) => {
      console.error(JSON.stringify({
        level: 'error',
        correlationId,
        message,
        ...data,
        timestamp: new Date().toISOString(),
      }));
    },
    warn: (message: string, data?: Record<string, unknown>) => {
      console.warn(JSON.stringify({
        level: 'warn',
        correlationId,
        message,
        ...data,
        timestamp: new Date().toISOString(),
      }));
    },
  };
}
