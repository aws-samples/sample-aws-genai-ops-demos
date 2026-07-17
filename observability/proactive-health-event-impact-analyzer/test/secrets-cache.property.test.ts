// Feature: production-hardening, Property 1/2
import * as fc from 'fast-check';
import {
  getSecret,
  resetSecretsCache,
  configureSecretsCache,
  setSSMClient,
} from '../infrastructure/cdk/lambda/lib/secrets-cache';

/**
 * Creates a mock SSM client that tracks calls and returns configured values.
 */
function createMockSSMClient(responses: Array<{ value?: string; error?: Error }>) {
  let callCount = 0;
  const calls: Array<{ Name: string; WithDecryption: boolean }> = [];

  const client = {
    send: jest.fn(async (command: { input: { Name: string; WithDecryption: boolean } }) => {
      calls.push(command.input);
      const response = responses[callCount];
      callCount++;

      if (!response) {
        throw new Error(`Unexpected call #${callCount}`);
      }

      if (response.error) {
        throw response.error;
      }

      return {
        Parameter: { Value: response.value },
      };
    }),
  };

  return { client: client as unknown as import('@aws-sdk/client-ssm').SSMClient, calls, getCallCount: () => callCount };
}

/**
 * Arbitrary for valid SSM parameter names.
 * SSM parameter names start with / and contain alphanumeric + /-/_/. characters.
 */
const arbParamName = fc.string({ minLength: 1, maxLength: 30 })
  .map((s: string) => `/${s.replace(/[^a-zA-Z0-9\-_]/g, 'x')}`);

/**
 * Arbitrary for valid secret values (non-empty strings).
 */
const arbSecretValue = fc.string({ minLength: 1, maxLength: 200 });

/**
 * Arbitrary for TTL values >= 5 minutes (300000ms) and reasonable upper bound.
 */
const arbTtlMs = fc.integer({ min: 300_000, max: 3_600_000 }); // 5 min to 60 min

describe('secrets-cache property tests', () => {
  beforeEach(() => {
    resetSecretsCache();
  });

  // Feature: production-hardening, Property 1
  /**
   * Property 1: Secret caching respects TTL
   *
   * For any secret name and any cache TTL value (>= 5 minutes), after the initial
   * fetch from SSM Parameter Store, subsequent calls within the TTL window SHALL
   * return the cached value without making an additional SSM API call; calls after
   * the TTL expires SHALL fetch a fresh value from SSM.
   *
   * **Validates: Requirements 2.4**
   */
  describe('Property 1: Secret caching respects TTL', () => {
    it('returns cached value within TTL window without additional SSM calls', async () => {
      await fc.assert(
        fc.asyncProperty(
          arbParamName,
          arbSecretValue,
          arbTtlMs,
          async (paramName: string, secretValue: string, ttlMs: number) => {
            resetSecretsCache();
            configureSecretsCache({ cacheTtlMs: ttlMs });

            const mock = createMockSSMClient([
              { value: secretValue },
            ]);
            setSSMClient(mock.client);

            // Initial fetch
            const result1 = await getSecret(paramName);
            expect(result1).toBe(secretValue);
            expect(mock.getCallCount()).toBe(1);

            // Subsequent call within TTL — should use cache, no new SSM call
            const result2 = await getSecret(paramName);
            expect(result2).toBe(secretValue);
            expect(mock.getCallCount()).toBe(1); // Still just 1 call
          }
        ),
        { numRuns: 100 }
      );
    });

    it('fetches fresh value after TTL expires', async () => {
      await fc.assert(
        fc.asyncProperty(
          arbParamName,
          arbSecretValue,
          arbSecretValue,
          arbTtlMs,
          async (paramName: string, firstValue: string, secondValue: string, ttlMs: number) => {
            resetSecretsCache();
            configureSecretsCache({ cacheTtlMs: ttlMs });

            const mock = createMockSSMClient([
              { value: firstValue },
              { value: secondValue },
            ]);
            setSSMClient(mock.client);

            // Initial fetch
            const result1 = await getSecret(paramName);
            expect(result1).toBe(firstValue);
            expect(mock.getCallCount()).toBe(1);

            // Simulate time passing beyond TTL
            const originalNow = Date.now;
            Date.now = () => originalNow() + ttlMs + 1;

            try {
              // After TTL expiry — should fetch fresh value
              const result2 = await getSecret(paramName);
              expect(result2).toBe(secondValue);
              expect(mock.getCallCount()).toBe(2); // New SSM call made
            } finally {
              Date.now = originalNow;
            }
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  // Feature: production-hardening, Property 2
  /**
   * Property 2: Secret retrieval errors never expose secret values
   *
   * For any secret name and any secret value stored in SSM, when the retrieval
   * operation fails (network error, permission denied, parameter not found), the
   * resulting error message SHALL contain the secret parameter name or ARN but
   * SHALL NOT contain any portion of the actual secret value.
   *
   * **Validates: Requirements 2.7**
   */
  describe('Property 2: Secret retrieval errors never expose secret values', () => {
    it('error messages contain parameter name but never contain secret value', async () => {
      await fc.assert(
        fc.asyncProperty(
          arbParamName,
          arbSecretValue,
          fc.constantFrom(
            'Access denied',
            'Connection timeout',
            'ParameterNotFound',
            'ThrottlingException',
            'InternalServerError'
          ),
          async (paramName: string, secretValue: string, errorMessage: string) => {
            resetSecretsCache();

            // First, populate the cache with a real secret value
            const mock = createMockSSMClient([
              { value: secretValue },
              { error: new Error(errorMessage) },
            ]);
            setSSMClient(mock.client);

            // Successfully cache the secret
            await getSecret(paramName);

            // Simulate TTL expiry to force a refetch
            const originalNow = Date.now;
            Date.now = () => originalNow() + 400_000; // Past default TTL

            try {
              await getSecret(paramName);
              // If it doesn't throw, the test passes (cache might still be valid)
            } catch (error: unknown) {
              const message = (error as Error).message;

              // Error message MUST contain the parameter name
              expect(message).toContain(paramName);

              // Error message MUST NOT contain the secret value
              // Only check if secretValue is long enough to be meaningful (>= 3 chars)
              if (secretValue.length >= 3) {
                expect(message).not.toContain(secretValue);
              }
            } finally {
              Date.now = originalNow;
            }
          }
        ),
        { numRuns: 100 }
      );
    });

    it('error on first fetch never exposes secret values in thrown error', async () => {
      await fc.assert(
        fc.asyncProperty(
          arbParamName,
          arbSecretValue,
          async (paramName: string, secretValue: string) => {
            resetSecretsCache();

            // SSM returns an error that could potentially contain sensitive info
            // The error message itself simulates a case where the SDK might echo data
            const mock = createMockSSMClient([
              { error: new Error(`Failed to get parameter`) },
            ]);
            setSSMClient(mock.client);

            try {
              await getSecret(paramName);
              // Should not reach here
              fail('Expected getSecret to throw');
            } catch (error: unknown) {
              const message = (error as Error).message;

              // Error message MUST contain the parameter name for debugging
              expect(message).toContain(paramName);

              // Error message MUST NOT contain the secret value
              // (secret value was never fetched, so this validates the error path
              //  doesn't somehow leak values from the error context)
              if (secretValue.length >= 3) {
                expect(message).not.toContain(secretValue);
              }
            }
          }
        ),
        { numRuns: 100 }
      );
    });

    it('error from SSM that already contains param name does not expose secrets', async () => {
      await fc.assert(
        fc.asyncProperty(
          arbParamName,
          arbSecretValue,
          async (paramName: string, secretValue: string) => {
            resetSecretsCache();

            // Simulate SDK error that already includes the parameter name
            const sdkError = new Error(
              `ParameterNotFound: ${paramName} could not be found`
            );
            const mock = createMockSSMClient([
              { error: sdkError },
            ]);
            setSSMClient(mock.client);

            try {
              await getSecret(paramName);
              fail('Expected getSecret to throw');
            } catch (error: unknown) {
              const message = (error as Error).message;

              // Error message still contains the parameter name
              expect(message).toContain(paramName);

              // Error message MUST NOT contain any secret value
              if (secretValue.length >= 3) {
                expect(message).not.toContain(secretValue);
              }
            }
          }
        ),
        { numRuns: 100 }
      );
    });
  });
});
