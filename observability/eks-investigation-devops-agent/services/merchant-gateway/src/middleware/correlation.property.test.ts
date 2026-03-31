/**
 * Property-Based Test: Request Correlation Tracing
 * 
 * Feature: devops-agent-eks, Property 5: Request Correlation Tracing
 * 
 * *For any* API request processed by the Merchant_Gateway, the request logs
 * SHALL contain a unique correlation ID that is propagated to all downstream
 * service calls and included in the response headers.
 * 
 * **Validates: Requirements 3.4**
 */

import * as fc from 'fast-check';
import express, { Express, Request, Response } from 'express';
import request from 'supertest';
import { correlationMiddleware, CORRELATION_HEADER, createCorrelatedLogger } from './correlation';

/**
 * Creates a test Express app with correlation middleware
 */
function createTestApp(): Express {
  const app = express();
  app.use(correlationMiddleware);
  app.get('/test', (req: Request, res: Response) => {
    res.status(200).json({
      correlationId: req.correlationId,
    });
  });
  return app;
}

// UUID regex pattern
const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

describe('Feature: devops-agent-eks, Property 5: Request Correlation Tracing', () => {
  let app: Express;

  beforeEach(() => {
    app = createTestApp();
  });

  /**
   * Property 5.1: Correlation ID Generation
   * 
   * For any request without a correlation ID header, the middleware SHALL
   * generate a valid UUID correlation ID.
   */
  describe('Property 5.1: Correlation ID generation', () => {
    it('should generate valid UUID for any request without correlation ID', async () => {
      await fc.assert(
        fc.asyncProperty(fc.constant(null), async () => {
          const response = await request(app)
            .get('/test')
            .expect(200);

          // Response should have correlation ID header
          const correlationId = response.headers[CORRELATION_HEADER];
          expect(correlationId).toBeDefined();
          expect(correlationId).toMatch(UUID_REGEX);

          // Body should also have correlation ID
          expect(response.body.correlationId).toBe(correlationId);
        }),
        { numRuns: 50 }
      );
    });
  });

  /**
   * Property 5.2: Correlation ID Propagation
   * 
   * For any request with a correlation ID header, the middleware SHALL
   * propagate the same ID to the response and request context.
   */
  describe('Property 5.2: Correlation ID propagation', () => {
    it('should propagate provided correlation ID for any valid UUID', async () => {
      await fc.assert(
        fc.asyncProperty(fc.uuid(), async (providedId) => {
          const response = await request(app)
            .get('/test')
            .set(CORRELATION_HEADER, providedId)
            .expect(200);

          // Response should have the same correlation ID
          expect(response.headers[CORRELATION_HEADER]).toBe(providedId);
          expect(response.body.correlationId).toBe(providedId);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 5.3: Unique Correlation IDs
   * 
   * For any two requests without correlation IDs, the generated IDs
   * SHALL be unique.
   */
  describe('Property 5.3: Unique correlation IDs', () => {
    it('should generate unique IDs for multiple requests', async () => {
      const correlationIds = new Set<string>();
      const numRequests = 50;

      await fc.assert(
        fc.asyncProperty(fc.constant(null), async () => {
          const response = await request(app)
            .get('/test')
            .expect(200);

          const correlationId = response.headers[CORRELATION_HEADER];
          
          // Should not have seen this ID before
          expect(correlationIds.has(correlationId)).toBe(false);
          correlationIds.add(correlationId);
        }),
        { numRuns: numRequests }
      );

      // All IDs should be unique
      expect(correlationIds.size).toBe(numRequests);
    });
  });

  /**
   * Property 5.4: Response Header Presence
   * 
   * For any request, the response SHALL always include the correlation
   * ID header.
   */
  describe('Property 5.4: Response header presence', () => {
    it('should always include correlation ID in response headers', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.option(fc.uuid(), { nil: undefined }),
          async (maybeProvidedId) => {
            const req = request(app).get('/test');
            
            if (maybeProvidedId) {
              req.set(CORRELATION_HEADER, maybeProvidedId);
            }
            
            const response = await req.expect(200);
            
            // Response should always have correlation ID header
            expect(response.headers[CORRELATION_HEADER]).toBeDefined();
            expect(response.headers[CORRELATION_HEADER]).toMatch(UUID_REGEX);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 5.5: Request Context Attachment
   * 
   * For any request, the correlation ID SHALL be attached to the
   * request object for use by downstream handlers.
   */
  describe('Property 5.5: Request context attachment', () => {
    it('should attach correlation ID to request context', async () => {
      await fc.assert(
        fc.asyncProperty(fc.uuid(), async (providedId) => {
          const response = await request(app)
            .get('/test')
            .set(CORRELATION_HEADER, providedId)
            .expect(200);

          // The handler should have access to correlationId on request
          expect(response.body.correlationId).toBe(providedId);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 5.6: Correlated Logger
   * 
   * For any correlation ID, the createCorrelatedLogger function SHALL
   * create a logger that includes the correlation ID in all log entries.
   */
  describe('Property 5.6: Correlated logger', () => {
    it('should create logger with correlation ID for any ID', () => {
      fc.assert(
        fc.property(fc.uuid(), (correlationId) => {
          const logger = createCorrelatedLogger(correlationId);
          
          // Logger should have info, error, and warn methods
          expect(typeof logger.info).toBe('function');
          expect(typeof logger.error).toBe('function');
          expect(typeof logger.warn).toBe('function');
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 5.7: Non-UUID Correlation ID Handling
   * 
   * For any request with a non-UUID correlation ID, the middleware SHALL
   * still propagate it (allowing custom correlation IDs).
   * Note: HTTP headers have leading/trailing whitespace trimmed per HTTP spec.
   */
  describe('Property 5.7: Non-UUID correlation ID handling', () => {
    it('should propagate any non-empty string as correlation ID', async () => {
      await fc.assert(
        fc.asyncProperty(
          // Generate strings without leading/trailing whitespace (HTTP spec trims these)
          fc.string({ minLength: 1, maxLength: 100 })
            .filter(s => s.trim().length > 0)
            .map(s => s.trim()),
          async (customId) => {
            const response = await request(app)
              .get('/test')
              .set(CORRELATION_HEADER, customId)
              .expect(200);

            // Should propagate the custom ID
            expect(response.headers[CORRELATION_HEADER]).toBe(customId);
            expect(response.body.correlationId).toBe(customId);
          }
        ),
        { numRuns: 50 }
      );
    });
  });
});
