/**
 * Property-Based Test: JWT Token Expiration
 * 
 * Feature: devops-agent-eks, Property 6: JWT Token Expiration
 * 
 * *For any* successful merchant login, the issued JWT access token SHALL have
 * an expiration time of exactly 1 hour from issuance, and the refresh token
 * SHALL successfully issue a new access token when the original expires.
 * 
 * **Validates: Requirements 4.4, 4.5**
 */

import * as fc from 'fast-check';
import {
  DEFAULT_JWT_CONFIG,
  validateTokenExpiration,
  isTokenExpired,
  calculateExpiration,
} from './jwt-config';
import {
  generateMockTokenPair,
  decodeToken,
  refreshTokens,
  getTokenExpirationInfo,
} from './jwt-utils';

// Test secret for JWT signing (only for testing)
const TEST_SECRET = 'test-secret-key-for-property-testing-only';

// Arbitrary generators for test data
const userIdArb = fc.uuid();
const emailArb = fc.emailAddress();
const merchantIdArb = fc.uuid();

describe('Feature: devops-agent-eks, Property 6: JWT Token Expiration', () => {
  /**
   * Property 6.1: Access Token Expiration Time
   * 
   * For any successful merchant login, the issued JWT access token SHALL have
   * an expiration time of exactly 1 hour (3600 seconds) from issuance.
   */
  describe('Property 6.1: Access Token has 1-hour expiration', () => {
    it('should generate access tokens with exactly 1-hour validity for any merchant', () => {
      fc.assert(
        fc.property(
          userIdArb,
          emailArb,
          merchantIdArb,
          (userId, email, merchantId) => {
            // Generate token pair for a merchant
            const tokenPair = generateMockTokenPair(
              userId,
              email,
              merchantId,
              TEST_SECRET
            );

            // Decode the access token
            const decoded = decodeToken(tokenPair.accessToken);
            expect(decoded).not.toBeNull();

            if (decoded) {
              // Verify the token validity is exactly 1 hour (3600 seconds)
              const validity = decoded.exp - decoded.iat;
              expect(validity).toBe(DEFAULT_JWT_CONFIG.accessTokenValiditySeconds);
              expect(validity).toBe(3600); // 1 hour in seconds
            }
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should validate access token expiration within tolerance for any issuance time', () => {
      fc.assert(
        fc.property(
          fc.integer({ min: 1609459200, max: 1893456000 }), // Unix timestamps from 2021 to 2030
          (issuedAt) => {
            const expectedValidity = DEFAULT_JWT_CONFIG.accessTokenValiditySeconds;
            const expiresAt = calculateExpiration(issuedAt, expectedValidity);

            // Validate that the expiration matches expected validity
            const isValid = validateTokenExpiration(
              issuedAt,
              expiresAt,
              expectedValidity,
              0 // Zero tolerance for exact match
            );

            expect(isValid).toBe(true);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 6.2: ID Token Expiration Time
   * 
   * For any successful merchant login, the issued JWT ID token SHALL have
   * an expiration time of exactly 1 hour (3600 seconds) from issuance.
   */
  describe('Property 6.2: ID Token has 1-hour expiration', () => {
    it('should generate ID tokens with exactly 1-hour validity for any merchant', () => {
      fc.assert(
        fc.property(
          userIdArb,
          emailArb,
          merchantIdArb,
          (userId, email, merchantId) => {
            const tokenPair = generateMockTokenPair(
              userId,
              email,
              merchantId,
              TEST_SECRET
            );

            const decoded = decodeToken(tokenPair.idToken);
            expect(decoded).not.toBeNull();

            if (decoded) {
              const validity = decoded.exp - decoded.iat;
              expect(validity).toBe(DEFAULT_JWT_CONFIG.idTokenValiditySeconds);
              expect(validity).toBe(3600);
            }
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 6.3: Refresh Token Validity
   * 
   * For any successful merchant login, the refresh token SHALL have
   * a validity of 30 days (2592000 seconds).
   */
  describe('Property 6.3: Refresh Token has 30-day validity', () => {
    it('should generate refresh tokens with 30-day validity for any merchant', () => {
      fc.assert(
        fc.property(
          userIdArb,
          emailArb,
          merchantIdArb,
          (userId, email, merchantId) => {
            const tokenPair = generateMockTokenPair(
              userId,
              email,
              merchantId,
              TEST_SECRET
            );

            const decoded = decodeToken(tokenPair.refreshToken);
            expect(decoded).not.toBeNull();

            if (decoded) {
              const validity = decoded.exp - decoded.iat;
              expect(validity).toBe(DEFAULT_JWT_CONFIG.refreshTokenValiditySeconds);
              expect(validity).toBe(2592000); // 30 days in seconds
            }
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 6.4: Refresh Token Issues New Access Token
   * 
   * For any valid refresh token, when used to refresh, it SHALL successfully
   * issue a new access token with the same 1-hour validity.
   */
  describe('Property 6.4: Refresh token issues new access token', () => {
    it('should issue new access token with 1-hour validity when refreshing', () => {
      fc.assert(
        fc.property(
          userIdArb,
          emailArb,
          merchantIdArb,
          (userId, email, merchantId) => {
            // Generate initial token pair
            const tokenPair = generateMockTokenPair(
              userId,
              email,
              merchantId,
              TEST_SECRET
            );

            // Use refresh token to get new tokens
            const refreshResult = refreshTokens(
              tokenPair.refreshToken,
              TEST_SECRET
            );

            expect(refreshResult).not.toBeNull();

            if (refreshResult) {
              // Verify new access token has 1-hour validity
              const newAccessInfo = getTokenExpirationInfo(refreshResult.accessToken);
              expect(newAccessInfo).not.toBeNull();

              if (newAccessInfo) {
                expect(newAccessInfo.validitySeconds).toBe(3600);
              }

              // Verify expiresIn matches
              expect(refreshResult.expiresIn).toBe(3600);
            }
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 6.5: Token Expiration Detection
   * 
   * For any token, the isTokenExpired function SHALL correctly identify
   * whether the token is expired based on the current time.
   */
  describe('Property 6.5: Token expiration detection is accurate', () => {
    it('should correctly detect expired tokens for any expiration time', () => {
      fc.assert(
        fc.property(
          fc.integer({ min: 1609459200, max: 1893456000 }), // expiration timestamp
          fc.integer({ min: -86400, max: 86400 }), // offset from expiration (-1 day to +1 day)
          (expiresAt, offset) => {
            const currentTime = expiresAt + offset;
            const expired = isTokenExpired(expiresAt, currentTime);

            // Token is expired if current time >= expiration time
            if (offset >= 0) {
              expect(expired).toBe(true);
            } else {
              expect(expired).toBe(false);
            }
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 6.6: Token Pair Consistency
   * 
   * For any merchant login, all tokens in the pair SHALL be issued at
   * the same time (within a small tolerance).
   */
  describe('Property 6.6: Token pair issuance time consistency', () => {
    it('should issue all tokens at the same time for any merchant', () => {
      fc.assert(
        fc.property(
          userIdArb,
          emailArb,
          merchantIdArb,
          (userId, email, merchantId) => {
            const tokenPair = generateMockTokenPair(
              userId,
              email,
              merchantId,
              TEST_SECRET
            );

            const accessInfo = getTokenExpirationInfo(tokenPair.accessToken);
            const idInfo = getTokenExpirationInfo(tokenPair.idToken);
            const refreshInfo = getTokenExpirationInfo(tokenPair.refreshToken);

            expect(accessInfo).not.toBeNull();
            expect(idInfo).not.toBeNull();
            expect(refreshInfo).not.toBeNull();

            if (accessInfo && idInfo && refreshInfo) {
              // All tokens should be issued at the same time (within 1 second tolerance)
              expect(Math.abs(accessInfo.issuedAt - idInfo.issuedAt)).toBeLessThanOrEqual(1);
              expect(Math.abs(accessInfo.issuedAt - refreshInfo.issuedAt)).toBeLessThanOrEqual(1);
            }
          }
        ),
        { numRuns: 100 }
      );
    });
  });
});
