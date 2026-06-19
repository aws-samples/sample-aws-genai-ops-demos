/**
 * Idempotency token generation for the DevOps Agent Tool Interface.
 *
 * Generates deterministic, collision-resistant tokens from DevOps Agent session identifiers.
 * Uses SHA-256 hashing to ensure:
 * - Determinism: the same session_id always produces the same token
 * - Collision resistance: distinct session_ids produce distinct tokens (via SHA-256 properties)
 *
 * Requirements: 1.5
 */

import { createHash } from "crypto";

/**
 * Generates a deterministic idempotency token from a DevOps Agent session identifier.
 *
 * The token is computed as the SHA-256 hex digest of the session_id string.
 * This guarantees that identical session_ids always produce identical tokens,
 * and different session_ids produce different tokens with overwhelming probability
 * (SHA-256 collision resistance).
 *
 * @param sessionId - The DevOps Agent session identifier string
 * @returns A 64-character hex string representing the idempotency token
 */
export function generateIdempotencyToken(sessionId: string): string {
  return createHash("sha256").update(sessionId).digest("hex");
}
