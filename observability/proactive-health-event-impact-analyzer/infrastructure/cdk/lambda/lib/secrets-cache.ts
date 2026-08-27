import { SSMClient, GetParameterCommand } from '@aws-sdk/client-ssm';

/**
 * Configuration for the secrets cache.
 */
export interface SecretsCacheConfig {
  /** Cache TTL in milliseconds. Minimum 300000 (5 minutes). */
  cacheTtlMs: number;
}

/**
 * Represents a cached secret value with its fetch timestamp.
 */
export interface CachedSecret {
  /** The decrypted secret value. */
  value: string;
  /** Unix timestamp (ms) when the secret was fetched from SSM. */
  fetchedAt: number;
}

const MIN_TTL_MS = 300_000; // 5 minutes

const DEFAULT_CONFIG: SecretsCacheConfig = {
  cacheTtlMs: MIN_TTL_MS,
};

// In-memory cache keyed by SSM parameter name
const cache = new Map<string, CachedSecret>();

// SSM client instance (lazily created)
let ssmClient: SSMClient | undefined;

// Configurable settings
let config: SecretsCacheConfig = { ...DEFAULT_CONFIG };

/**
 * Configure the secrets cache. The TTL is clamped to a minimum of 5 minutes.
 */
export function configureSecretsCache(options: Partial<SecretsCacheConfig>): void {
  config = {
    cacheTtlMs: Math.max(options.cacheTtlMs ?? DEFAULT_CONFIG.cacheTtlMs, MIN_TTL_MS),
  };
}

/**
 * Retrieve a secret from SSM Parameter Store with in-memory caching.
 *
 * On cache hit within the TTL window, returns the cached value without
 * making an SSM API call. After TTL expiry, fetches a fresh value.
 *
 * Error messages include the parameter name but never expose the secret value.
 *
 * @param paramName - The SSM Parameter Store parameter name (path)
 * @returns The decrypted secret value
 * @throws Error if the parameter cannot be retrieved from SSM
 */
export async function getSecret(paramName: string): Promise<string> {
  const now = Date.now();
  const cached = cache.get(paramName);

  if (cached && (now - cached.fetchedAt) < config.cacheTtlMs) {
    return cached.value;
  }

  // Lazily initialize SSM client
  if (!ssmClient) {
    ssmClient = new SSMClient({});
  }

  try {
    const response = await ssmClient.send(
      new GetParameterCommand({
        Name: paramName,
        WithDecryption: true,
      })
    );

    const value = response.Parameter?.Value;
    if (value === undefined || value === null) {
      throw new Error(
        `SSM parameter "${paramName}" returned no value`
      );
    }

    const entry: CachedSecret = { value, fetchedAt: now };
    cache.set(paramName, entry);

    return value;
  } catch (error: unknown) {
    // Ensure error messages include param name but never expose the secret value
    const message = error instanceof Error ? error.message : String(error);

    // If the error already contains the param name (e.g., from SSM SDK), rethrow as-is
    if (message.includes(paramName)) {
      throw error;
    }

    throw new Error(
      `Failed to retrieve SSM parameter "${paramName}": ${message}`
    );
  }
}

/**
 * Clear the secrets cache. Useful for testing or forced refresh.
 */
export function clearSecretsCache(): void {
  cache.clear();
}

/**
 * Reset the secrets cache module entirely (cache + config + client).
 * Primarily used in tests.
 */
export function resetSecretsCache(): void {
  cache.clear();
  config = { ...DEFAULT_CONFIG };
  ssmClient = undefined;
}

/**
 * Inject a custom SSM client (for testing).
 */
export function setSSMClient(client: SSMClient): void {
  ssmClient = client;
}
