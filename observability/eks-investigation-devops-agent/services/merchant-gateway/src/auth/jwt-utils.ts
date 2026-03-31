/**
 * JWT Utilities for Token Generation and Validation
 * 
 * This module provides utilities for working with JWT tokens
 * in the context of Cognito authentication.
 */

import * as jwt from 'jsonwebtoken';
import { JwtConfig, DEFAULT_JWT_CONFIG, isTokenExpired } from './jwt-config';

export interface TokenPayload {
  sub: string;
  email: string;
  'cognito:username': string;
  'cognito:groups'?: string[];
  'custom:merchant_id'?: string;
  token_use: 'access' | 'id';
  iat: number;
  exp: number;
  iss: string;
  aud?: string;
}

export interface TokenPair {
  accessToken: string;
  idToken: string;
  refreshToken: string;
  expiresIn: number;
}

export interface RefreshResult {
  accessToken: string;
  idToken: string;
  expiresIn: number;
}

/**
 * Generates a mock JWT token for testing purposes
 * This simulates what Cognito would generate
 */
export function generateMockToken(
  payload: Partial<TokenPayload>,
  secret: string,
  validitySeconds: number
): string {
  const now = Math.floor(Date.now() / 1000);
  const fullPayload: TokenPayload = {
    sub: payload.sub || 'test-user-id',
    email: payload.email || 'test@example.com',
    'cognito:username': payload['cognito:username'] || 'testuser',
    'cognito:groups': payload['cognito:groups'] || ['Merchants'],
    'custom:merchant_id': payload['custom:merchant_id'] || 'merchant-123',
    token_use: payload.token_use || 'access',
    iat: payload.iat || now,
    exp: payload.exp || now + validitySeconds,
    iss: payload.iss || 'https://cognito-idp.us-east-1.amazonaws.com/test-pool',
    aud: payload.aud,
  };

  return jwt.sign(fullPayload, secret, { algorithm: 'HS256' });
}

/**
 * Generates a complete token pair (access, id, refresh) for testing
 */
export function generateMockTokenPair(
  userId: string,
  email: string,
  merchantId: string,
  secret: string,
  config: Omit<JwtConfig, 'issuer' | 'audience'> = DEFAULT_JWT_CONFIG
): TokenPair {
  const now = Math.floor(Date.now() / 1000);
  
  const accessToken = generateMockToken(
    {
      sub: userId,
      email,
      'cognito:username': email,
      'custom:merchant_id': merchantId,
      token_use: 'access',
      iat: now,
      exp: now + config.accessTokenValiditySeconds,
    },
    secret,
    config.accessTokenValiditySeconds
  );

  const idToken = generateMockToken(
    {
      sub: userId,
      email,
      'cognito:username': email,
      'custom:merchant_id': merchantId,
      token_use: 'id',
      iat: now,
      exp: now + config.idTokenValiditySeconds,
    },
    secret,
    config.idTokenValiditySeconds
  );

  // Refresh token is opaque in Cognito, we simulate it
  const refreshToken = jwt.sign(
    {
      sub: userId,
      token_use: 'refresh',
      iat: now,
      exp: now + config.refreshTokenValiditySeconds,
    },
    secret,
    { algorithm: 'HS256' }
  );

  return {
    accessToken,
    idToken,
    refreshToken,
    expiresIn: config.accessTokenValiditySeconds,
  };
}

/**
 * Decodes a JWT token without verification (for inspection)
 */
export function decodeToken(token: string): TokenPayload | null {
  try {
    return jwt.decode(token) as TokenPayload;
  } catch {
    return null;
  }
}

/**
 * Verifies a JWT token with the given secret
 */
export function verifyToken(token: string, secret: string): TokenPayload | null {
  try {
    return jwt.verify(token, secret) as TokenPayload;
  } catch {
    return null;
  }
}

/**
 * Simulates refreshing tokens using a refresh token
 * Returns new access and id tokens if the refresh token is valid
 */
export function refreshTokens(
  refreshToken: string,
  secret: string,
  config: Omit<JwtConfig, 'issuer' | 'audience'> = DEFAULT_JWT_CONFIG
): RefreshResult | null {
  try {
    const decoded = jwt.verify(refreshToken, secret) as {
      sub: string;
      token_use: string;
      exp: number;
    };

    // Check if refresh token is valid and not expired
    if (decoded.token_use !== 'refresh' || isTokenExpired(decoded.exp)) {
      return null;
    }

    const now = Math.floor(Date.now() / 1000);
    
    // Generate new access token
    const accessToken = generateMockToken(
      {
        sub: decoded.sub,
        token_use: 'access',
        iat: now,
        exp: now + config.accessTokenValiditySeconds,
      },
      secret,
      config.accessTokenValiditySeconds
    );

    // Generate new id token
    const idToken = generateMockToken(
      {
        sub: decoded.sub,
        token_use: 'id',
        iat: now,
        exp: now + config.idTokenValiditySeconds,
      },
      secret,
      config.idTokenValiditySeconds
    );

    return {
      accessToken,
      idToken,
      expiresIn: config.accessTokenValiditySeconds,
    };
  } catch {
    return null;
  }
}

/**
 * Extracts token expiration info
 */
export function getTokenExpirationInfo(token: string): {
  issuedAt: number;
  expiresAt: number;
  validitySeconds: number;
  isExpired: boolean;
} | null {
  const decoded = decodeToken(token);
  if (!decoded) return null;

  return {
    issuedAt: decoded.iat,
    expiresAt: decoded.exp,
    validitySeconds: decoded.exp - decoded.iat,
    isExpired: isTokenExpired(decoded.exp),
  };
}
