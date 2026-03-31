/**
 * AWS X-Ray Tracing Configuration for Webhook Service
 * Requirements: 10.4
 * 
 * Configures X-Ray SDK for distributed tracing across the webhook service.
 * Provides automatic instrumentation for HTTP requests and SQS message processing.
 */

import AWSXRay from 'aws-xray-sdk-core';

// Service name for X-Ray traces
const SERVICE_NAME = process.env.XRAY_SERVICE_NAME || 'webhook-service';
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

  // Configure plugins for EC2/ECS/EKS metadata
  AWSXRay.config([AWSXRay.plugins.ECSPlugin]);

  // Capture HTTP/HTTPS for outbound webhook calls
  AWSXRay.captureHTTPsGlobal(require('http'));
  AWSXRay.captureHTTPsGlobal(require('https'));

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
 * Create a new segment for processing a message
 */
export function createMessageSegment(messageId: string, transactionId?: string): AWSXRay.Segment | null {
  if (process.env.NODE_ENV === 'test') {
    return null;
  }

  try {
    const segment = new AWSXRay.Segment(`${SERVICE_NAME}-${ENVIRONMENT}`);
    segment.addAnnotation('messageId', messageId);
    
    if (transactionId) {
      segment.addAnnotation('transactionId', transactionId);
    }

    AWSXRay.setSegment(segment);
    return segment;
  } catch (error) {
    console.error('Failed to create X-Ray segment:', error);
    return null;
  }
}

/**
 * Close a segment
 */
export function closeSegment(segment: AWSXRay.Segment | null): void {
  if (segment) {
    try {
      segment.close();
    } catch (error) {
      console.error('Failed to close X-Ray segment:', error);
    }
  }
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
    try {
      subsegment.close();
    } catch (error) {
      console.error('Failed to close X-Ray subsegment:', error);
    }
  }
}

/**
 * Add error to current segment
 */
export function addError(error: Error): void {
  if (process.env.NODE_ENV === 'test') {
    return;
  }

  try {
    const segment = AWSXRay.getSegment();
    if (segment) {
      segment.addError(error);
    }
  } catch (err) {
    // Silently ignore if no segment is available
  }
}

/**
 * Capture AWS SDK v3 clients for automatic tracing
 */
export function captureAWSv3Client<T>(client: T): T {
  if (process.env.NODE_ENV === 'test') {
    return client;
  }

  return AWSXRay.captureAWSv3Client(client as any) as T;
}

export { AWSXRay };
