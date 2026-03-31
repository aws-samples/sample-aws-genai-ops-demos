/**
 * Property-Based Test: Authentication Enforcement
 * 
 * Feature: devops-agent-eks, Property 1: Authentication Enforcement
 * 
 * *For any* request to a protected API endpoint, if the request lacks a valid
 * Cognito JWT token or the token is expired, the system SHALL return a 401
 * Unauthorized response and not process the request.
 * 
 * **Validates: Requirements 1.1, 1.8, 3.1, 3.5**
 */

import * as fc from 'fast-check';
import express, { Express, Request, Response, NextFunction } from 'express';
import request from 'supertest';
import * as jwt from 'jsonwebtoken';
import {
  jwtAuthMiddleware,
  extractToken,
  validateTokenStructure,
  extractMerchantIdentity,
} from './jwt-auth';
import { correlationMiddleware } from './correlation';

// Test secret for JWT signing
// nosemgrep: javascript.jsonwebtoken.security.jwt-hardcode.hardcoded-jwt-secret -- test-only secret, not used in production
const TEST_SECRET = 'test-secret-key-for-property-testing';

// Set environment variable for test mode
process.env.JWT_SECRET = TEST_SECRET;

/**
 * Creates a test Express app with JWT auth middleware
 */
function createTestApp(): Express {
  const app = express();
  app.use(express.json());
  app.use(correlationMiddleware);
  app.use('/protected', jwtAuthMiddleware);
  app.get('/protected/resource', (req: Request, res: Response) => {
    res.status(200).json({
      success: true,
      merchant: req.merchant,
    });
  });
  return app;
}

/**
 * Generates a valid JWT token
 */
function generateValidToken(payload: Record<string, unknown> = {}): string {
  const now = Math.floor(Date.now() / 1000);
  return jwt.sign(
    {
      sub: payload.sub || 'test-user-id',
      email: payload.email || 'test@example.com',
      'cognito:username': payload.username || 'testuser',
      'custom:merchant_id': payload.merchantId || 'merchant-123',
      token_use: 'access',
      iat: now,
      exp: now + 3600, // 1 hour
      iss: 'https://cognito-idp.us-east-1.amazonaws.com/test-pool',
      ...payload,
    },
    TEST_SECRET // nosemgrep: javascript.jsonwebtoken.security.jwt-hardcode.hardcoded-jwt-secret
  );
}

/**
 * Generates an expired JWT token
 */
function generateExpiredToken(payload: Record<string, unknown> = {}): string {
  const now = Math.floor(Date.now() / 1000);
  return jwt.sign(
    {
      sub: payload.sub || 'test-user-id',
      email: payload.email || 'test@example.com',
      'cognito:username': payload.username || 'testuser',
      'custom:merchant_id': payload.merchantId || 'merchant-123',
      token_use: 'access',
      iat: now - 7200, // 2 hours ago
      exp: now - 3600, // Expired 1 hour ago
      iss: 'https://cognito-idp.us-east-1.amazonaws.com/test-pool',
      ...payload,
    },
    TEST_SECRET // nosemgrep: javascript.jsonwebtoken.security.jwt-hardcode.hardcoded-jwt-secret
  );
}

// Arbitrary generators
const userIdArb = fc.uuid();
const emailArb = fc.emailAddress();
const merchantIdArb = fc.uuid();
const usernameArb = fc.string({ minLength: 3, maxLength: 20 }).filter(s => /^[a-zA-Z0-9]+$/.test(s));

describe('Feature: devops-agent-eks, Property 1: Authentication Enforcement', () => {
  let app: Express;

  beforeEach(() => {
    app = createTestApp();
  });

  /**
   * Property 1.1: Missing Token Returns 401
   * 
   * For any request without an Authorization header, the system SHALL
   * return a 401 Unauthorized response.
   */
  describe('Property 1.1: Missing token returns 401', () => {
    it('should return 401 for any request without Authorization header', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.constant(null), // No specific input needed
          async () => {
            const response = await request(app)
              .get('/protected/resource')
              .expect(401);

            expect(response.body.error).toBeDefined();
            expect(response.body.error.code).toBe('AUTH_TOKEN_MISSING');
          }
        ),
        { numRuns: 10 }
      );
    });
  });

  /**
   * Property 1.2: Invalid Token Format Returns 401
   * 
   * For any request with a malformed Authorization header, the system
   * SHALL return a 401 Unauthorized response.
   */
  describe('Property 1.2: Invalid token format returns 401', () => {
    it('should return 401 for any malformed Authorization header', async () => {
      const invalidAuthHeaders = fc.oneof(
        fc.constant(''),
        fc.constant('Bearer'),
        fc.constant('Bearer '),
        fc.constant('Basic abc123'),
        fc.string({ minLength: 1, maxLength: 50 }),
        fc.constant('Bearer invalid.token'),
        fc.constant('Bearer a.b'),
      );

      await fc.assert(
        fc.asyncProperty(invalidAuthHeaders, async (authHeader) => {
          const response = await request(app)
            .get('/protected/resource')
            .set('Authorization', authHeader)
            .expect(401);

          expect(response.body.error).toBeDefined();
          expect(['AUTH_TOKEN_MISSING', 'AUTH_TOKEN_INVALID']).toContain(
            response.body.error.code
          );
        }),
        { numRuns: 50 }
      );
    });
  });

  /**
   * Property 1.3: Expired Token Returns 401
   * 
   * For any request with an expired JWT token, the system SHALL return
   * a 401 Unauthorized response with AUTH_TOKEN_EXPIRED code.
   */
  describe('Property 1.3: Expired token returns 401', () => {
    it('should return 401 for any expired token', async () => {
      await fc.assert(
        fc.asyncProperty(
          userIdArb,
          emailArb,
          merchantIdArb,
          async (userId, email, merchantId) => {
            const expiredToken = generateExpiredToken({
              sub: userId,
              email,
              merchantId,
            });

            const response = await request(app)
              .get('/protected/resource')
              .set('Authorization', `Bearer ${expiredToken}`)
              .expect(401);

            expect(response.body.error).toBeDefined();
            expect(response.body.error.code).toBe('AUTH_TOKEN_EXPIRED');
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 1.4: Valid Token Allows Access
   * 
   * For any request with a valid JWT token, the system SHALL allow
   * access and extract merchant identity.
   */
  describe('Property 1.4: Valid token allows access', () => {
    it('should allow access for any valid token and extract merchant identity', async () => {
      await fc.assert(
        fc.asyncProperty(
          userIdArb,
          emailArb,
          merchantIdArb,
          async (userId, email, merchantId) => {
            const validToken = generateValidToken({
              sub: userId,
              email,
              merchantId,
            });

            const response = await request(app)
              .get('/protected/resource')
              .set('Authorization', `Bearer ${validToken}`)
              .expect(200);

            expect(response.body.success).toBe(true);
            expect(response.body.merchant).toBeDefined();
            expect(response.body.merchant.sub).toBe(userId);
            expect(response.body.merchant.merchantId).toBe(merchantId);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 1.5: Token Extraction
   * 
   * For any valid Bearer token format, extractToken SHALL correctly
   * extract the token string.
   */
  describe('Property 1.5: Token extraction', () => {
    it('should correctly extract token from any valid Bearer format', () => {
      fc.assert(
        fc.property(
          fc.string({ minLength: 10, maxLength: 500 }).filter(s => /^[a-zA-Z0-9]+$/.test(s)),
          (tokenString) => {
            const authHeader = `Bearer ${tokenString}`;
            const extracted = extractToken(authHeader);
            expect(extracted).toBe(tokenString);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should return null for missing or invalid headers', () => {
      expect(extractToken(undefined)).toBeNull();
      expect(extractToken('')).toBeNull();
      expect(extractToken('Basic abc')).toBeNull();
      expect(extractToken('Bearer')).toBeNull();
    });
  });

  /**
   * Property 1.6: Merchant Identity Extraction
   * 
   * For any valid JWT payload, extractMerchantIdentity SHALL correctly
   * extract all merchant fields.
   */
  describe('Property 1.6: Merchant identity extraction', () => {
    it('should correctly extract merchant identity from any valid payload', () => {
      fc.assert(
        fc.property(
          userIdArb,
          emailArb,
          merchantIdArb,
          usernameArb,
          (sub, email, merchantId, username) => {
            const payload = {
              sub,
              email,
              'cognito:username': username,
              'custom:merchant_id': merchantId,
              'cognito:groups': ['Merchants'],
            };

            const identity = extractMerchantIdentity(payload);

            expect(identity.sub).toBe(sub);
            expect(identity.email).toBe(email);
            expect(identity.merchantId).toBe(merchantId);
            expect(identity.username).toBe(username);
            expect(identity.groups).toContain('Merchants');
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 1.7: Response Contains Correlation ID
   * 
   * For any authentication failure, the error response SHALL contain
   * a correlation ID for tracing.
   */
  describe('Property 1.7: Error responses contain correlation ID', () => {
    it('should include correlation ID in all error responses', async () => {
      await fc.assert(
        fc.asyncProperty(fc.uuid(), async (correlationId) => {
          const response = await request(app)
            .get('/protected/resource')
            .set('X-Correlation-ID', correlationId)
            .expect(401);

          expect(response.body.error.correlationId).toBe(correlationId);
          expect(response.headers['x-correlation-id']).toBe(correlationId);
        }),
        { numRuns: 50 }
      );
    });
  });
});
