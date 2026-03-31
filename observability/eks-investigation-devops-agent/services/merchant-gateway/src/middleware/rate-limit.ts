/**
 * Rate Limiting Middleware
 * 
 * Enforces rate limits per merchant using a sliding window algorithm.
 * Returns 429 Too Many Requests when limits are exceeded.
 * 
 * Requirements: 3.3, 3.6
 */

import { Request, Response, NextFunction } from 'express';
import { createCorrelatedLogger } from './correlation';

/**
 * Rate limit configuration
 */
export interface RateLimitConfig {
  /** Maximum requests per window */
  maxRequests: number;
  /** Window size in milliseconds */
  windowMs: number;
}

/**
 * Rate limit entry for tracking requests
 */
interface RateLimitEntry {
  /** Request timestamps within the window */
  timestamps: number[];
  /** Custom limit for this merchant (optional) */
  customLimit?: number;
}

// In-memory store for rate limiting
const rateLimitStore: Map<string, RateLimitEntry> = new Map();

// Default configuration: 100 requests per minute
const DEFAULT_CONFIG: RateLimitConfig = {
  maxRequests: parseInt(process.env.RATE_LIMIT_MAX || '100', 10),
  windowMs: parseInt(process.env.RATE_LIMIT_WINDOW_MS || '60000', 10), // 1 minute
};

/**
 * Cleans up expired timestamps from an entry
 */
function cleanupExpiredTimestamps(entry: RateLimitEntry, windowMs: number): void {
  const now = Date.now();
  const cutoff = now - windowMs;
  entry.timestamps = entry.timestamps.filter(ts => ts > cutoff);
}

/**
 * Gets the current request count for a merchant within the window
 */
export function getRequestCount(merchantId: string, config: RateLimitConfig = DEFAULT_CONFIG): number {
  const entry = rateLimitStore.get(merchantId);
  if (!entry) return 0;
  
  cleanupExpiredTimestamps(entry, config.windowMs);
  return entry.timestamps.length;
}

/**
 * Records a request for a merchant
 */
export function recordRequest(merchantId: string): void {
  const entry = rateLimitStore.get(merchantId) || { timestamps: [] };
  entry.timestamps.push(Date.now());
  rateLimitStore.set(merchantId, entry);
}

/**
 * Checks if a merchant has exceeded their rate limit
 */
export function isRateLimited(
  merchantId: string,
  config: RateLimitConfig = DEFAULT_CONFIG
): { limited: boolean; remaining: number; resetMs: number } {
  const entry = rateLimitStore.get(merchantId);
  
  if (!entry) {
    return {
      limited: false,
      remaining: config.maxRequests,
      resetMs: config.windowMs,
    };
  }
  
  cleanupExpiredTimestamps(entry, config.windowMs);
  
  const limit = entry.customLimit || config.maxRequests;
  const count = entry.timestamps.length;
  const remaining = Math.max(0, limit - count);
  
  // Calculate time until oldest request expires
  let resetMs = config.windowMs;
  if (entry.timestamps.length > 0) {
    const oldestTimestamp = Math.min(...entry.timestamps);
    resetMs = Math.max(0, (oldestTimestamp + config.windowMs) - Date.now());
  }
  
  return {
    limited: count >= limit,
    remaining,
    resetMs,
  };
}

/**
 * Sets a custom rate limit for a specific merchant
 */
export function setMerchantRateLimit(merchantId: string, limit: number): void {
  const entry = rateLimitStore.get(merchantId) || { timestamps: [] };
  entry.customLimit = limit;
  rateLimitStore.set(merchantId, entry);
}

/**
 * Clears rate limit data for a merchant (for testing)
 */
export function clearRateLimitData(merchantId?: string): void {
  if (merchantId) {
    rateLimitStore.delete(merchantId);
  } else {
    rateLimitStore.clear();
  }
}

/**
 * Gets all rate limit entries (for testing/debugging)
 */
export function getRateLimitStore(): Map<string, RateLimitEntry> {
  return new Map(rateLimitStore);
}

/**
 * Creates error response for rate limit exceeded
 */
function createRateLimitError(
  res: Response,
  correlationId: string,
  retryAfterMs: number
): void {
  const retryAfterSeconds = Math.ceil(retryAfterMs / 1000);
  
  res.setHeader('Retry-After', retryAfterSeconds.toString());
  res.setHeader('X-RateLimit-Remaining', '0');
  
  res.status(429).json({
    error: {
      code: 'RATE_LIMIT_EXCEEDED',
      message: 'Too many requests. Please try again later.',
      correlationId,
      retryAfter: retryAfterSeconds,
      timestamp: new Date().toISOString(),
    },
  });
}

/**
 * Rate Limiting Middleware
 * 
 * Tracks requests per merchant and enforces rate limits.
 * Requires JWT authentication middleware to run first.
 */
export function rateLimitMiddleware(
  req: Request,
  res: Response,
  next: NextFunction
): void {
  const correlationId = req.correlationId || 'unknown';
  const logger = createCorrelatedLogger(correlationId);
  
  // Get merchant ID from authenticated request
  const merchantId = req.merchant?.merchantId;
  
  if (!merchantId) {
    // If no merchant ID, skip rate limiting (auth should have failed)
    next();
    return;
  }
  
  // Check rate limit
  const { limited, remaining, resetMs } = isRateLimited(merchantId);
  
  // Add rate limit headers
  res.setHeader('X-RateLimit-Limit', DEFAULT_CONFIG.maxRequests.toString());
  res.setHeader('X-RateLimit-Remaining', remaining.toString());
  res.setHeader('X-RateLimit-Reset', Math.ceil(resetMs / 1000).toString());
  
  if (limited) {
    logger.warn('Rate limit exceeded', { merchantId, resetMs });
    createRateLimitError(res, correlationId, resetMs);
    return;
  }
  
  // Record this request
  recordRequest(merchantId);
  
  logger.info('Request within rate limit', { merchantId, remaining: remaining - 1 });
  next();
}

/**
 * Creates a rate limit middleware with custom configuration
 */
export function createRateLimitMiddleware(config: Partial<RateLimitConfig>) {
  const mergedConfig: RateLimitConfig = { ...DEFAULT_CONFIG, ...config };
  
  return (req: Request, res: Response, next: NextFunction): void => {
    const correlationId = req.correlationId || 'unknown';
    const logger = createCorrelatedLogger(correlationId);
    
    const merchantId = req.merchant?.merchantId;
    
    if (!merchantId) {
      next();
      return;
    }
    
    const { limited, remaining, resetMs } = isRateLimited(merchantId, mergedConfig);
    
    res.setHeader('X-RateLimit-Limit', mergedConfig.maxRequests.toString());
    res.setHeader('X-RateLimit-Remaining', remaining.toString());
    res.setHeader('X-RateLimit-Reset', Math.ceil(resetMs / 1000).toString());
    
    if (limited) {
      logger.warn('Rate limit exceeded', { merchantId, resetMs });
      createRateLimitError(res, correlationId, resetMs);
      return;
    }
    
    recordRequest(merchantId);
    logger.info('Request within rate limit', { merchantId, remaining: remaining - 1 });
    next();
  };
}
