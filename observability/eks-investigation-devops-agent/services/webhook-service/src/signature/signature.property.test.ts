/**
 * Property-Based Tests for Webhook Signature Service
 * 
 * Feature: devops-agent-eks, Property 8: Webhook Signature Authenticity
 * 
 * For any webhook payload delivered to a merchant endpoint, the payload SHALL be
 * signed with HMAC-SHA256 using the merchant's webhook secret, and the signature
 * SHALL be verifiable by the merchant using the same algorithm and secret.
 * 
 * **Validates: Requirements 5.5**
 */

import * as fc from 'fast-check';
import { SignatureService } from './signature-service';

describe('Property 8: Webhook Signature Authenticity', () => {
  const signatureService = new SignatureService();

  /**
   * Property: For any payload and secret, generating a signature and then
   * verifying it with the same payload and secret should always succeed.
   */
  it('should verify signatures generated with the same payload and secret (round-trip)', () => {
    fc.assert(
      fc.property(
        fc.object({ maxDepth: 3 }),
        fc.string({ minLength: 1, maxLength: 256 }),
        (payload, secret) => {
          const signature = signatureService.generateSignature(payload, secret);
          const isValid = signatureService.verifySignature(payload, signature, secret);
          return isValid === true;
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: For any payload and two different secrets, the signatures
   * generated should be different.
   */
  it('should generate different signatures for different secrets', () => {
    fc.assert(
      fc.property(
        fc.object({ maxDepth: 3 }),
        fc.string({ minLength: 1, maxLength: 256 }),
        fc.string({ minLength: 1, maxLength: 256 }),
        (payload, secret1, secret2) => {
          fc.pre(secret1 !== secret2);
          
          const signature1 = signatureService.generateSignature(payload, secret1);
          const signature2 = signatureService.generateSignature(payload, secret2);
          
          return signature1 !== signature2;
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: For any payload and secret, verification with a wrong secret
   * should always fail.
   */
  it('should fail verification when using wrong secret', () => {
    fc.assert(
      fc.property(
        fc.object({ maxDepth: 3 }),
        fc.string({ minLength: 1, maxLength: 256 }),
        fc.string({ minLength: 1, maxLength: 256 }),
        (payload, correctSecret, wrongSecret) => {
          fc.pre(correctSecret !== wrongSecret);
          
          const signature = signatureService.generateSignature(payload, correctSecret);
          const isValid = signatureService.verifySignature(payload, signature, wrongSecret);
          
          return isValid === false;
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: For any payload and secret, the same payload should always
   * produce the same signature (deterministic).
   */
  it('should generate deterministic signatures for the same payload and secret', () => {
    fc.assert(
      fc.property(
        fc.object({ maxDepth: 3 }),
        fc.string({ minLength: 1, maxLength: 256 }),
        (payload, secret) => {
          const signature1 = signatureService.generateSignature(payload, secret);
          const signature2 = signatureService.generateSignature(payload, secret);
          
          return signature1 === signature2;
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: For any payload and secret, the generated signature should
   * be a valid hex string of 64 characters (SHA-256 produces 32 bytes = 64 hex chars).
   */
  it('should generate valid hex signatures of correct length', () => {
    fc.assert(
      fc.property(
        fc.object({ maxDepth: 3 }),
        fc.string({ minLength: 1, maxLength: 256 }),
        (payload, secret) => {
          const signature = signatureService.generateSignature(payload, secret);
          
          // SHA-256 produces 32 bytes = 64 hex characters
          const isCorrectLength = signature.length === 64;
          const isValidHex = /^[0-9a-f]{64}$/.test(signature);
          
          return isCorrectLength && isValidHex;
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: For any payload and secret, verification with a tampered
   * payload should always fail.
   */
  it('should fail verification when payload is tampered', () => {
    fc.assert(
      fc.property(
        fc.object({ maxDepth: 2 }),
        fc.string({ minLength: 1, maxLength: 256 }),
        fc.string({ minLength: 1 }),
        (originalPayload, secret, tamperedValue) => {
          const signature = signatureService.generateSignature(originalPayload, secret);
          
          // Create a tampered payload by adding a new field
          const tamperedPayload = { ...originalPayload, _tampered: tamperedValue };
          
          const isValid = signatureService.verifySignature(tamperedPayload, signature, secret);
          
          return isValid === false;
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: The signature header format should always be parseable back
   * to the original signature.
   */
  it('should format and parse signature headers correctly (round-trip)', () => {
    fc.assert(
      fc.property(
        fc.hexaString({ minLength: 64, maxLength: 64 }),
        (signature) => {
          const headerValue = signatureService.formatSignatureHeader(signature);
          const parsedSignature = signatureService.parseSignatureHeader(headerValue);
          
          return parsedSignature === signature;
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: Invalid header formats should return null when parsed.
   */
  it('should return null for invalid signature header formats', () => {
    fc.assert(
      fc.property(
        fc.string().filter(s => !s.startsWith('sha256=')),
        (invalidHeader) => {
          const parsed = signatureService.parseSignatureHeader(invalidHeader);
          return parsed === null;
        }
      ),
      { numRuns: 100 }
    );
  });

  /**
   * Property: String payloads should produce the same signature as their
   * object equivalent when JSON stringified.
   */
  it('should produce consistent signatures for string and object payloads', () => {
    fc.assert(
      fc.property(
        fc.object({ maxDepth: 3 }),
        fc.string({ minLength: 1, maxLength: 256 }),
        (payload, secret) => {
          const signatureFromObject = signatureService.generateSignature(payload, secret);
          const signatureFromString = signatureService.generateSignature(JSON.stringify(payload), secret);
          
          return signatureFromObject === signatureFromString;
        }
      ),
      { numRuns: 100 }
    );
  });
});
