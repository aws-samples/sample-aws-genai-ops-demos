/**
 * JWT Authentication Middleware
 * 
 * Validates Cognito JWT tokens and extracts merchant identity.
 * Returns 401 for invalid or missing tokens.
 * 
 * Requirements: 3.1, 3.5
 */

import { Request, Response, NextFunction } from 'express';
import * as jwt from 'jsonwebtoken';
import jwksClient, { JwksClient, SigningKey } from 'jwks-rsa';
import { createCorrelatedLogger } from './correlation';

export interface MerchantIdentity {
  sub: string;
  email: string;
  merchantId: string;
  username: string;
  groups: string[];
}

declare global {
  namespace Express {
    interface Request {
      merchant?: MerchantIdentity;
    }
  }
}

// JWKS client cache
let jwksClientInstance: JwksClient | null = null;

// In-memory key cache
const keyCache: Map<string, string> = new Map();
const KEY_CACHE_TTL_MS = 3600000; // 1 hour
const keyCacheTimestamps: Map<string, number> = new Map();

/**
 * Configuration for JWT validation
 */
export interface JwtAuthConfig {
  userPoolId: string;
  region: string;
  clientId: string;
}

// Default config from environment
const defaultConfig: JwtAuthConfig = {
  userPoolId: process.env.COGNITO_USER_POOL_ID || '',
  region: process.env.AWS_REGION || 'us-east-1',
  clientId: process.env.COGNITO_CLIENT_ID || '',
};

/**
 * Gets or creates the JWKS client
 */
function getJwksClient(config: JwtAuthConfig): JwksClient {
  if (!jwksClientInstance) {
    const jwksUri = `https://cognito-idp.${config.region}.amazonaws.com/${config.userPoolId}/.well-known/jwks.json`;
    jwksClientInstance = jwksClient({
      jwksUri,
      cache: true,
      cacheMaxAge: 3600000, // 1 hour
      rateLimit: true,
      jwksRequestsPerMinute: 10,
    });
  }
  return jwksClientInstance;
}

/**
 * Gets signing key from JWKS with caching
 */
async function getSigningKey(kid: string, config: JwtAuthConfig): Promise<string> {
  // Check cache first
  const cachedKey = keyCache.get(kid);
  const cacheTimestamp = keyCacheTimestamps.get(kid);
  
  if (cachedKey && cacheTimestamp && Date.now() - cacheTimestamp < KEY_CACHE_TTL_MS) {
    return cachedKey;
  }

  const client = getJwksClient(config);
  const key: SigningKey = await client.getSigningKey(kid);
  const signingKey = key.getPublicKey();
  
  // Cache the key
  keyCache.set(kid, signingKey);
  keyCacheTimestamps.set(kid, Date.now());
  
  return signingKey;
}

/**
 * Extracts token from Authorization header
 */
export function extractToken(authHeader: string | undefined): string | null {
  if (!authHeader) return null;
  
  const parts = authHeader.split(' ');
  if (parts.length !== 2 || parts[0].toLowerCase() !== 'bearer') {
    return null;
  }
  
  return parts[1];
}

/**
 * Validates JWT token structure (without signature verification)
 * Note: In dev mode (JWT_SECRET set), kid is not required as we use symmetric signing
 */
export function validateTokenStructure(token: string): { valid: boolean; error?: string } {
  const parts = token.split('.');
  if (parts.length !== 3) {
    return { valid: false, error: 'Invalid token format' };
  }
  
  try {
    const header = JSON.parse(Buffer.from(parts[0], 'base64url').toString());
    if (!header.alg) {
      return { valid: false, error: 'Missing required header fields' };
    }
    // kid is only required for production (JWKS-based validation)
    // In dev mode with JWT_SECRET, symmetric signing doesn't include kid
    if (!process.env.JWT_SECRET && !header.kid) {
      return { valid: false, error: 'Missing required header fields' };
    }
    
    const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString());
    if (!payload.sub || !payload.exp) {
      return { valid: false, error: 'Missing required payload fields' };
    }
    
    return { valid: true };
  } catch {
    return { valid: false, error: 'Malformed token' };
  }
}

/**
 * Extracts merchant identity from token payload
 */
export function extractMerchantIdentity(payload: jwt.JwtPayload): MerchantIdentity {
  return {
    sub: payload.sub || '',
    email: payload.email || '',
    merchantId: payload['custom:merchant_id'] || payload.sub || '',
    username: payload['cognito:username'] || payload.email || '',
    groups: payload['cognito:groups'] || [],
  };
}

/**
 * Creates error response for authentication failures
 */
function createAuthError(
  res: Response,
  code: string,
  message: string,
  correlationId: string
): void {
  res.status(401).json({
    error: {
      code,
      message,
      correlationId,
      timestamp: new Date().toISOString(),
    },
  });
}

/**
 * JWT Authentication Middleware
 * 
 * Validates Cognito JWT tokens from the Authorization header.
 * On success, attaches merchant identity to the request.
 * On failure, returns 401 Unauthorized.
 */
export function jwtAuthMiddleware(
  req: Request,
  res: Response,
  next: NextFunction
): void {
  const correlationId = req.correlationId || 'unknown';
  const logger = createCorrelatedLogger(correlationId);

  const token = extractToken(req.headers.authorization);
  
  if (!token) {
    logger.warn('Missing authorization token');
    createAuthError(res, 'AUTH_TOKEN_MISSING', 'No authorization token provided', correlationId);
    return;
  }

  // Validate token structure
  const structureValidation = validateTokenStructure(token);
  if (!structureValidation.valid) {
    logger.warn('Invalid token structure', { error: structureValidation.error });
    createAuthError(res, 'AUTH_TOKEN_INVALID', structureValidation.error || 'Invalid token', correlationId);
    return;
  }

  // For testing/development without Cognito
  if (process.env.JWT_SECRET) {
    try {
      const payload = jwt.verify(token, process.env.JWT_SECRET) as jwt.JwtPayload;
      req.merchant = extractMerchantIdentity(payload);
      logger.info('Token validated (dev mode)', { merchantId: req.merchant.merchantId });
      next();
      return;
    } catch (err) {
      const error = err as jwt.JsonWebTokenError;
      if (error.name === 'TokenExpiredError') {
        logger.warn('Token expired');
        createAuthError(res, 'AUTH_TOKEN_EXPIRED', 'Token has expired', correlationId);
        return;
      }
      logger.warn('Token validation failed', { error: error.message });
      createAuthError(res, 'AUTH_TOKEN_INVALID', 'Invalid token', correlationId);
      return;
    }
  }

  // Production: Validate with Cognito JWKS
  validateWithCognito(token, defaultConfig, correlationId)
    .then((merchant) => {
      req.merchant = merchant;
      logger.info('Token validated', { merchantId: merchant.merchantId });
      next();
    })
    .catch((err) => {
      const error = err as Error;
      logger.warn('Cognito validation failed', { error: error.message });
      
      if (error.message.includes('expired')) {
        createAuthError(res, 'AUTH_TOKEN_EXPIRED', 'Token has expired', correlationId);
      } else {
        createAuthError(res, 'AUTH_TOKEN_INVALID', 'Invalid token', correlationId);
      }
    });
}

/**
 * Validates token with Cognito JWKS
 */
async function validateWithCognito(
  token: string,
  config: JwtAuthConfig,
  correlationId: string
): Promise<MerchantIdentity> {
  const logger = createCorrelatedLogger(correlationId);
  
  // Decode header to get kid
  const decoded = jwt.decode(token, { complete: true });
  if (!decoded || !decoded.header.kid) {
    throw new Error('Unable to decode token header');
  }

  // Get signing key
  const signingKey = await getSigningKey(decoded.header.kid, config);
  
  // Verify token
  const payload = jwt.verify(token, signingKey, {
    issuer: `https://cognito-idp.${config.region}.amazonaws.com/${config.userPoolId}`,
    algorithms: ['RS256'],
  }) as jwt.JwtPayload;

  // Validate token_use
  if (payload.token_use !== 'access') {
    throw new Error('Invalid token_use');
  }

  logger.info('Token verified with Cognito');
  return extractMerchantIdentity(payload);
}

/**
 * Clears the JWKS client cache (for testing)
 */
export function clearJwksCache(): void {
  jwksClientInstance = null;
  keyCache.clear();
  keyCacheTimestamps.clear();
}
