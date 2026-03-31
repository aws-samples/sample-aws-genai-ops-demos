/**
 * Middleware exports
 */

export { correlationMiddleware, createCorrelatedLogger, CORRELATION_HEADER } from './correlation';
export { jwtAuthMiddleware, extractToken, validateTokenStructure, extractMerchantIdentity, clearJwksCache } from './jwt-auth';
export type { MerchantIdentity, JwtAuthConfig } from './jwt-auth';
export { 
  rateLimitMiddleware, 
  createRateLimitMiddleware,
  isRateLimited, 
  recordRequest, 
  getRequestCount,
  setMerchantRateLimit,
  clearRateLimitData,
  getRateLimitStore
} from './rate-limit';
export type { RateLimitConfig } from './rate-limit';
