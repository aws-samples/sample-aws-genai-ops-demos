/**
 * Signature Service - HMAC-SHA256 signature generation for webhooks
 * Requirements: 5.5
 * 
 * Property 8: Webhook Signature Authenticity
 * For any webhook payload delivered to a merchant endpoint, the payload SHALL be
 * signed with HMAC-SHA256 using the merchant's webhook secret, and the signature
 * SHALL be verifiable by the merchant using the same algorithm and secret.
 */

import crypto from 'crypto';

export class SignatureService {
  /**
   * Generate HMAC-SHA256 signature for a payload
   * @param payload - The payload to sign (will be JSON stringified if object)
   * @param secret - The merchant's webhook secret
   * @returns The hex-encoded HMAC-SHA256 signature
   */
  generateSignature(payload: string | object, secret: string): string {
    const payloadString = typeof payload === 'string' 
      ? payload 
      : JSON.stringify(payload);
    
    const hmac = crypto.createHmac('sha256', secret);
    hmac.update(payloadString, 'utf8');
    return hmac.digest('hex');
  }

  /**
   * Verify a signature against a payload and secret
   * @param payload - The payload that was signed
   * @param signature - The signature to verify
   * @param secret - The merchant's webhook secret
   * @returns true if the signature is valid, false otherwise
   */
  verifySignature(payload: string | object, signature: string, secret: string): boolean {
    const expectedSignature = this.generateSignature(payload, secret);
    
    // Use timing-safe comparison to prevent timing attacks
    try {
      return crypto.timingSafeEqual(
        Buffer.from(signature, 'hex'),
        Buffer.from(expectedSignature, 'hex')
      );
    } catch {
      // If buffers have different lengths, timingSafeEqual throws
      return false;
    }
  }

  /**
   * Create the signature header value
   * Format: sha256=<signature>
   * @param signature - The hex-encoded signature
   * @returns The formatted header value
   */
  formatSignatureHeader(signature: string): string {
    return `sha256=${signature}`;
  }

  /**
   * Parse a signature header value
   * @param headerValue - The header value in format "sha256=<signature>"
   * @returns The extracted signature or null if invalid format
   */
  parseSignatureHeader(headerValue: string): string | null {
    const prefix = 'sha256=';
    if (!headerValue.startsWith(prefix)) {
      return null;
    }
    return headerValue.slice(prefix.length);
  }
}
