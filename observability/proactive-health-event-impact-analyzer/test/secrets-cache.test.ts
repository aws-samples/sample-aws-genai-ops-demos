import { SSMClient, GetParameterCommand } from '@aws-sdk/client-ssm';
import {
  getSecret,
  clearSecretsCache,
  resetSecretsCache,
  configureSecretsCache,
  setSSMClient,
} from '../infrastructure/cdk/lambda/lib/secrets-cache';

// Mock the AWS SDK SSM client
jest.mock('@aws-sdk/client-ssm', () => {
  const mockSend = jest.fn();
  return {
    SSMClient: jest.fn(() => ({ send: mockSend })),
    GetParameterCommand: jest.fn((input: unknown) => ({ input })),
    __mockSend: mockSend,
  };
});

// Get access to the mock send function
const { __mockSend: mockSend } = jest.requireMock('@aws-sdk/client-ssm') as { __mockSend: jest.Mock };

describe('secrets-cache', () => {
  beforeEach(() => {
    resetSecretsCache();
    mockSend.mockReset();
  });

  describe('getSecret', () => {
    it('fetches a secret from SSM Parameter Store with decryption', async () => {
      mockSend.mockResolvedValueOnce({
        Parameter: { Value: 'my-secret-value' },
      });

      const result = await getSecret('/health-analyzer/production/webhook-secret');

      expect(result).toBe('my-secret-value');
      expect(GetParameterCommand).toHaveBeenCalledWith({
        Name: '/health-analyzer/production/webhook-secret',
        WithDecryption: true,
      });
    });

    it('returns cached value on subsequent calls within TTL', async () => {
      mockSend.mockResolvedValueOnce({
        Parameter: { Value: 'cached-secret' },
      });

      const result1 = await getSecret('/my/param');
      const result2 = await getSecret('/my/param');

      expect(result1).toBe('cached-secret');
      expect(result2).toBe('cached-secret');
      expect(mockSend).toHaveBeenCalledTimes(1);
    });

    it('fetches fresh value after TTL expires', async () => {
      // Configure a very short TTL for testing
      configureSecretsCache({ cacheTtlMs: 300_000 });

      mockSend
        .mockResolvedValueOnce({ Parameter: { Value: 'first-value' } })
        .mockResolvedValueOnce({ Parameter: { Value: 'second-value' } });

      const result1 = await getSecret('/my/param');
      expect(result1).toBe('first-value');

      // Manually expire the cache by manipulating Date.now
      const originalNow = Date.now;
      Date.now = () => originalNow() + 300_001; // 5 minutes + 1ms

      try {
        const result2 = await getSecret('/my/param');
        expect(result2).toBe('second-value');
        expect(mockSend).toHaveBeenCalledTimes(2);
      } finally {
        Date.now = originalNow;
      }
    });

    it('throws an error when SSM returns no value', async () => {
      mockSend.mockResolvedValueOnce({
        Parameter: { Value: undefined },
      });

      await expect(getSecret('/missing/param'))
        .rejects.toThrow('SSM parameter "/missing/param" returned no value');
    });

    it('throws an error that includes the parameter name on SSM failure', async () => {
      mockSend.mockRejectedValueOnce(new Error('Access denied'));

      await expect(getSecret('/forbidden/param'))
        .rejects.toThrow('/forbidden/param');
    });

    it('never exposes secret values in error messages', async () => {
      const secretValue = 'super-secret-webhook-url-123';
      mockSend.mockResolvedValueOnce({
        Parameter: { Value: secretValue },
      });

      // First call succeeds (populates cache)
      await getSecret('/my/secret');

      // Now simulate a failure on refresh after TTL expires
      const originalNow = Date.now;
      Date.now = () => originalNow() + 400_000;

      mockSend.mockRejectedValueOnce(new Error('Connection timeout'));

      try {
        await getSecret('/my/secret');
      } catch (error: unknown) {
        const message = (error as Error).message;
        expect(message).not.toContain(secretValue);
        expect(message).toContain('/my/secret');
      } finally {
        Date.now = originalNow;
      }
    });

    it('caches different secrets independently', async () => {
      mockSend
        .mockResolvedValueOnce({ Parameter: { Value: 'secret-a' } })
        .mockResolvedValueOnce({ Parameter: { Value: 'secret-b' } });

      const a = await getSecret('/param/a');
      const b = await getSecret('/param/b');

      expect(a).toBe('secret-a');
      expect(b).toBe('secret-b');
      expect(mockSend).toHaveBeenCalledTimes(2);

      // Subsequent calls should use cache
      const a2 = await getSecret('/param/a');
      const b2 = await getSecret('/param/b');
      expect(a2).toBe('secret-a');
      expect(b2).toBe('secret-b');
      expect(mockSend).toHaveBeenCalledTimes(2);
    });
  });

  describe('configureSecretsCache', () => {
    it('enforces minimum TTL of 5 minutes', async () => {
      configureSecretsCache({ cacheTtlMs: 1000 }); // 1 second - too short

      mockSend
        .mockResolvedValueOnce({ Parameter: { Value: 'val1' } });

      await getSecret('/my/param');

      // After 1 second, cache should still hold because min TTL is 5 min
      const originalNow = Date.now;
      Date.now = () => originalNow() + 2000;

      try {
        await getSecret('/my/param');
        expect(mockSend).toHaveBeenCalledTimes(1); // No additional call
      } finally {
        Date.now = originalNow;
      }
    });

    it('accepts TTL values above the minimum', () => {
      // Should not throw
      configureSecretsCache({ cacheTtlMs: 600_000 }); // 10 minutes
    });
  });

  describe('clearSecretsCache', () => {
    it('forces a fresh fetch on next call', async () => {
      mockSend
        .mockResolvedValueOnce({ Parameter: { Value: 'original' } })
        .mockResolvedValueOnce({ Parameter: { Value: 'refreshed' } });

      const result1 = await getSecret('/my/param');
      expect(result1).toBe('original');

      clearSecretsCache();

      const result2 = await getSecret('/my/param');
      expect(result2).toBe('refreshed');
      expect(mockSend).toHaveBeenCalledTimes(2);
    });
  });
});
