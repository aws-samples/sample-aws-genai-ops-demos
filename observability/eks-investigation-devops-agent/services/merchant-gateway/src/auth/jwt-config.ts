/**
 * JWT Configuration for Cognito Token Validation
 * 
 * This module defines the token validity configuration that matches
 * the Cognito User Pool settings defined in cognito.yaml
 */

export interface JwtConfig {
  /** Access token validity in seconds */
  accessTokenValiditySeconds: number;
  /** ID token validity in seconds */
  idTokenValiditySeconds: number;
  /** Refresh token validity in seconds */
  refreshTokenValiditySeconds: number;
  /** Issuer URL (Cognito User Pool URL) */
  issuer: string;
  /** Audience (App Client ID) */
  audience: string;
}

/**
 * Default JWT configuration matching Cognito User Pool settings
 * - Access Token: 1 hour (3600 seconds) - Requirements 4.4
 * - Refresh Token: 30 days (2592000 seconds) - Requirements 4.5
 */
export const DEFAULT_JWT_CONFIG: Omit<JwtConfig, 'issuer' | 'audience'> = {
  accessTokenValiditySeconds: 3600, // 1 hour
  idTokenValiditySeconds: 3600, // 1 hour
  refreshTokenValiditySeconds: 2592000, // 30 days
};

/**
 * Creates a JWT configuration with the specified Cognito settings
 */
export function createJwtConfig(
  userPoolId: string,
  clientId: string,
  region: string = 'us-east-1'
): JwtConfig {
  return {
    ...DEFAULT_JWT_CONFIG,
    issuer: `https://cognito-idp.${region}.amazonaws.com/${userPoolId}`,
    audience: clientId,
  };
}

/**
 * Validates that a token's expiration matches expected validity
 * @param issuedAt - Token issued at timestamp (seconds)
 * @param expiresAt - Token expiration timestamp (seconds)
 * @param expectedValiditySeconds - Expected validity duration in seconds
 * @param toleranceSeconds - Allowed tolerance for timing differences
 * @returns true if the token validity matches expected duration
 */
export function validateTokenExpiration(
  issuedAt: number,
  expiresAt: number,
  expectedValiditySeconds: number,
  toleranceSeconds: number = 5
): boolean {
  const actualValidity = expiresAt - issuedAt;
  return Math.abs(actualValidity - expectedValiditySeconds) <= toleranceSeconds;
}

/**
 * Checks if a token is expired
 * @param expiresAt - Token expiration timestamp (seconds)
 * @param currentTime - Current time (seconds), defaults to now
 * @returns true if the token is expired
 */
export function isTokenExpired(
  expiresAt: number,
  currentTime: number = Math.floor(Date.now() / 1000)
): boolean {
  return currentTime >= expiresAt;
}

/**
 * Calculates when a token will expire
 * @param issuedAt - Token issued at timestamp (seconds)
 * @param validitySeconds - Token validity duration in seconds
 * @returns Expiration timestamp in seconds
 */
export function calculateExpiration(
  issuedAt: number,
  validitySeconds: number
): number {
  return issuedAt + validitySeconds;
}
