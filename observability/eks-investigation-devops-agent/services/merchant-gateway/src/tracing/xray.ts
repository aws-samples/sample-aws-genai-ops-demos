/**
 * AWS X-Ray Tracing Configuration
 * Requirements: 10.4
 * 
 * Configures X-Ray SDK for distributed tracing across the merchant gateway service.
 * Provides automatic instrumentation for HTTP requests and custom segment creation.
 */

import AWSXRay from 'aws-xray-sdk-core';
import { Express, Request, Response, NextFunction } from 'express';

// Service name for X-Ray traces
const SERVICE_NAME = process.env.XRAY_SERVICE_NAME || 'merchant-gateway';
const ENVIRONMENT = process.env.ENVIRONMENT || 'dev';

/**
 * Initialize X-Ray SDK with service configuration
 */
export function initializeXRay(): void {
  // Only enable X-Ray in non-test environments
  if (process.env.NODE_ENV === 'test') {
    return;
  }

  // Configure X-Ray daemon address (default: localhost:2000)
  const daemonAddress = process.env.AWS_XRAY_DAEMON_ADDRESS || '127.0.0.1:2000';
  AWSXRay.setDaemonAddress(daemonAddress);

  // Set default segment name
  AWSXRay.setContextMissingStrategy('LOG_ERROR');

  // Configure plugins for EC2/ECS metadata
  // Note: EKS plugin is not available in aws-xray-sdk-core, using ECS plugin instead
  AWSXRay.config([AWSXRay.plugins.ECSPlugin]);

  console.log(JSON.stringify({
    level: 'info',
    message: 'X-Ray tracing initialized',
    service: SERVICE_NAME,
    environment: ENVIRONMENT,
    daemonAddress,
    timestamp: new Date().toISOString(),
  }));
}

/**
 * Express middleware for X-Ray tracing
 * Creates a segment for each incoming request
 */
export function xrayMiddleware(app: Express): void {
  if (process.env.NODE_ENV === 'test') {
    return;
  }

  // Manual segment creation for incoming requests
  app.use((req: Request, res: Response, next: NextFunction) => {
    const segment = new AWSXRay.Segment(`${SERVICE_NAME}-${ENVIRONMENT}`);
    const ns = AWSXRay.getNamespace();
    
    ns.run(() => {
      AWSXRay.setSegment(segment);
      segment.addAnnotation('service', SERVICE_NAME);
      
      res.on('finish', () => {
        segment.close();
      });
      
      next();
    });
  });
}

/**
 * Close X-Ray segment middleware
 * Should be added after all routes
 */
export function xrayCloseSegment(app: Express): void {
  if (process.env.NODE_ENV === 'test') {
    return;
  }

  // Segment closing is handled in xrayMiddleware
}

/**
 * Add custom annotation to current segment
 */
export function addAnnotation(key: string, value: string | number | boolean): void {
  if (process.env.NODE_ENV === 'test') {
    return;
  }

  try {
    const segment = AWSXRay.getSegment();
    if (segment) {
      segment.addAnnotation(key, value);
    }
  } catch (error) {
    // Silently ignore if no segment is available
  }
}

/**
 * Add custom metadata to current segment
 */
export function addMetadata(key: string, value: unknown, namespace?: string): void {
  if (process.env.NODE_ENV === 'test') {
    return;
  }

  try {
    const segment = AWSXRay.getSegment();
    if (segment) {
      segment.addMetadata(key, value, namespace || SERVICE_NAME);
    }
  } catch (error) {
    // Silently ignore if no segment is available
  }
}

/**
 * Create a subsegment for tracking specific operations
 */
export function createSubsegment(name: string): AWSXRay.Subsegment | null {
  if (process.env.NODE_ENV === 'test') {
    return null;
  }

  try {
    const segment = AWSXRay.getSegment();
    if (segment) {
      return segment.addNewSubsegment(name);
    }
  } catch (error) {
    // Silently ignore if no segment is available
  }
  return null;
}

/**
 * Close a subsegment
 */
export function closeSubsegment(subsegment: AWSXRay.Subsegment | null): void {
  if (subsegment) {
    subsegment.close();
  }
}

/**
 * Middleware to add correlation ID and merchant ID to X-Ray segment
 */
export function xrayAnnotationMiddleware(req: Request, res: Response, next: NextFunction): void {
  if (process.env.NODE_ENV === 'test') {
    return next();
  }

  const correlationId = req.headers['x-correlation-id'] as string;
  const merchantId = (req as any).merchantId;

  if (correlationId) {
    addAnnotation('correlationId', correlationId);
  }

  if (merchantId) {
    addAnnotation('merchantId', merchantId);
  }

  addAnnotation('httpMethod', req.method);
  addAnnotation('httpPath', req.path);

  next();
}

/**
 * Capture AWS SDK clients for automatic tracing
 */
export function captureAWSClient<T>(client: T): T {
  if (process.env.NODE_ENV === 'test') {
    return client;
  }

  return AWSXRay.captureAWSClient(client as any) as T;
}

/**
 * Capture HTTP/HTTPS modules for automatic tracing of outbound requests
 */
export function captureHTTPsGlobal(): void {
  if (process.env.NODE_ENV === 'test') {
    return;
  }

  AWSXRay.captureHTTPsGlobal(require('http'));
  AWSXRay.captureHTTPsGlobal(require('https'));
}

export { AWSXRay };
