/**
 * Property-Based Test: Rate Limiting Enforcement
 * 
 * Feature: devops-agent-eks, Property 4: Rate Limiting Enforcement
 * 
 * *For any* merchant, when the number of requests within a 1-minute window
 * exceeds the configured rate limit, subsequent requests SHALL receive a
 * 429 Too Many Requests response until the window resets.
 * 
 * **Validates: Requirements 3.3, 3.6**
 */

import * as fc from 'fast-check';
import {
  isRateLimited,
  recordRequest,
  getRequestCount,
  clearRateLimitData,
  setMerchantRateLimit,
  RateLimitConfig,
} from './rate-limit';

// Arbitrary generators
const merchantIdArb = fc.uuid();
const rateLimitArb = fc.integer({ min: 1, max: 1000 });
const requestCountArb = fc.integer({ min: 0, max: 500 });

describe('Feature: devops-agent-eks, Property 4: Rate Limiting Enforcement', () => {
  beforeEach(() => {
    clearRateLimitData();
  });

  afterEach(() => {
    clearRateLimitData();
  });

  /**
   * Property 4.1: Request Count Tracking
   * 
   * For any merchant and any number of requests, the rate limiter SHALL
   * accurately track the number of requests made.
   */
  describe('Property 4.1: Request count tracking', () => {
    it('should accurately track request count for any merchant', () => {
      fc.assert(
        fc.property(
          merchantIdArb,
          fc.integer({ min: 1, max: 100 }),
          (merchantId, numRequests) => {
            clearRateLimitData(merchantId);
            
            // Record requests
            for (let i = 0; i < numRequests; i++) {
              recordRequest(merchantId);
            }
            
            // Verify count
            const count = getRequestCount(merchantId);
            expect(count).toBe(numRequests);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 4.2: Rate Limit Enforcement
   * 
   * For any merchant with a configured rate limit, when the number of
   * requests equals or exceeds the limit, isRateLimited SHALL return true.
   */
  describe('Property 4.2: Rate limit enforcement', () => {
    it('should enforce rate limit when requests exceed limit', () => {
      fc.assert(
        fc.property(
          merchantIdArb,
          fc.integer({ min: 1, max: 50 }),
          (merchantId, limit) => {
            clearRateLimitData(merchantId);
            
            const config: RateLimitConfig = {
              maxRequests: limit,
              windowMs: 60000,
            };
            
            // Record exactly limit number of requests
            for (let i = 0; i < limit; i++) {
              recordRequest(merchantId);
            }
            
            // Should be rate limited
            const result = isRateLimited(merchantId, config);
            expect(result.limited).toBe(true);
            expect(result.remaining).toBe(0);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 4.3: Under Limit Allows Requests
   * 
   * For any merchant with requests below the limit, isRateLimited SHALL
   * return false and indicate remaining capacity.
   */
  describe('Property 4.3: Under limit allows requests', () => {
    it('should allow requests when under limit', () => {
      fc.assert(
        fc.property(
          merchantIdArb,
          fc.integer({ min: 10, max: 100 }),
          fc.integer({ min: 1, max: 9 }),
          (merchantId, limit, requestsMade) => {
            // Ensure requestsMade is less than limit
            const actualRequests = Math.min(requestsMade, limit - 1);
            
            clearRateLimitData(merchantId);
            
            const config: RateLimitConfig = {
              maxRequests: limit,
              windowMs: 60000,
            };
            
            // Record fewer requests than limit
            for (let i = 0; i < actualRequests; i++) {
              recordRequest(merchantId);
            }
            
            // Should not be rate limited
            const result = isRateLimited(merchantId, config);
            expect(result.limited).toBe(false);
            expect(result.remaining).toBe(limit - actualRequests);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 4.4: New Merchant Has Full Capacity
   * 
   * For any new merchant (no previous requests), isRateLimited SHALL
   * return false with full remaining capacity.
   */
  describe('Property 4.4: New merchant has full capacity', () => {
    it('should have full capacity for any new merchant', () => {
      fc.assert(
        fc.property(
          merchantIdArb,
          rateLimitArb,
          (merchantId, limit) => {
            clearRateLimitData(merchantId);
            
            const config: RateLimitConfig = {
              maxRequests: limit,
              windowMs: 60000,
            };
            
            const result = isRateLimited(merchantId, config);
            expect(result.limited).toBe(false);
            expect(result.remaining).toBe(limit);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 4.5: Custom Merchant Limits
   * 
   * For any merchant with a custom rate limit, the custom limit SHALL
   * be enforced instead of the default.
   */
  describe('Property 4.5: Custom merchant limits', () => {
    it('should enforce custom limits for any merchant', () => {
      fc.assert(
        fc.property(
          merchantIdArb,
          fc.integer({ min: 5, max: 50 }),
          fc.integer({ min: 100, max: 200 }),
          (merchantId, customLimit, defaultLimit) => {
            clearRateLimitData(merchantId);
            
            // Set custom limit (lower than default)
            setMerchantRateLimit(merchantId, customLimit);
            
            const config: RateLimitConfig = {
              maxRequests: defaultLimit,
              windowMs: 60000,
            };
            
            // Record exactly customLimit requests
            for (let i = 0; i < customLimit; i++) {
              recordRequest(merchantId);
            }
            
            // Should be rate limited at custom limit, not default
            const result = isRateLimited(merchantId, config);
            expect(result.limited).toBe(true);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 4.6: Remaining Count Accuracy
   * 
   * For any merchant and any number of requests below the limit,
   * the remaining count SHALL equal (limit - requests made).
   */
  describe('Property 4.6: Remaining count accuracy', () => {
    it('should accurately calculate remaining requests', () => {
      fc.assert(
        fc.property(
          merchantIdArb,
          fc.integer({ min: 10, max: 100 }),
          fc.integer({ min: 0, max: 9 }),
          (merchantId, limit, requestsMade) => {
            clearRateLimitData(merchantId);
            
            const config: RateLimitConfig = {
              maxRequests: limit,
              windowMs: 60000,
            };
            
            // Record requests
            for (let i = 0; i < requestsMade; i++) {
              recordRequest(merchantId);
            }
            
            const result = isRateLimited(merchantId, config);
            expect(result.remaining).toBe(limit - requestsMade);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 4.7: Merchant Isolation
   * 
   * For any two different merchants, rate limiting for one SHALL NOT
   * affect the other.
   */
  describe('Property 4.7: Merchant isolation', () => {
    it('should isolate rate limits between merchants', () => {
      fc.assert(
        fc.property(
          merchantIdArb,
          merchantIdArb,
          fc.integer({ min: 10, max: 50 }),
          (merchantId1, merchantId2, limit) => {
            // Skip if same merchant ID generated
            if (merchantId1 === merchantId2) return;
            
            clearRateLimitData();
            
            const config: RateLimitConfig = {
              maxRequests: limit,
              windowMs: 60000,
            };
            
            // Exhaust rate limit for merchant 1
            for (let i = 0; i < limit; i++) {
              recordRequest(merchantId1);
            }
            
            // Merchant 1 should be limited
            const result1 = isRateLimited(merchantId1, config);
            expect(result1.limited).toBe(true);
            
            // Merchant 2 should NOT be limited
            const result2 = isRateLimited(merchantId2, config);
            expect(result2.limited).toBe(false);
            expect(result2.remaining).toBe(limit);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 4.8: Monotonic Request Count
   * 
   * For any merchant, recording a request SHALL always increase
   * the request count by exactly 1.
   */
  describe('Property 4.8: Monotonic request count', () => {
    it('should increase count by 1 for each request', () => {
      fc.assert(
        fc.property(
          merchantIdArb,
          fc.integer({ min: 1, max: 50 }),
          (merchantId, numRequests) => {
            clearRateLimitData(merchantId);
            
            for (let i = 0; i < numRequests; i++) {
              const countBefore = getRequestCount(merchantId);
              recordRequest(merchantId);
              const countAfter = getRequestCount(merchantId);
              
              expect(countAfter).toBe(countBefore + 1);
            }
          }
        ),
        { numRuns: 100 }
      );
    });
  });
});
