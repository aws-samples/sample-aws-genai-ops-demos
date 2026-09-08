import { SecretsManagerClient, GetSecretValueCommand } from '@aws-sdk/client-secrets-manager';

/**
 * In-memory cache for AWS Secrets Manager secrets, keyed by secret ARN.
 *
 * Used for the DevOps Agent webhook HMAC secret specifically. That secret is
 * now provisioned by the CDK-managed DevOpsAgentSpace construct (see
 * lib/constructs/devops-agent-space.ts) into a Secrets Manager secret whose
 * value never passes through CloudFormation — as opposed to the Slack/MS
 * Teams webhook URLs, which remain plain SSM Parameter Store SecureStrings
 * (see secrets-cache.ts) since those are unrelated to the DevOps Agent
 * CLI-to-CDK migration.
 *
 * The secret may live in a different region than the Lambda consuming it —
 * an Agent Space (and its webhook secret) can be deployed to a different
 * region than the main stack. Each cache entry therefore carries its own
 * region-scoped client.
 */
export interface SecretsManagerCacheConfig {
  /** Cache TTL in milliseconds. Minimum 300000 (5 minutes). */
  cacheTtlMs: number;
}

interface CachedSecret {
  value: string;
  fetchedAt: number;
}

const MIN_TTL_MS = 300_000; // 5 minutes

const DEFAULT_CONFIG: SecretsManagerCacheConfig = {
  cacheTtlMs: MIN_TTL_MS,
};

const cache = new Map<string, CachedSecret>();
const clients = new Map<string, SecretsManagerClient>();

let config: SecretsManagerCacheConfig = { ...DEFAULT_CONFIG };

/**
 * Configure the secrets cache. The TTL is clamped to a minimum of 5 minutes.
 */
export function configureSecretsManagerCache(options: Partial<SecretsManagerCacheConfig>): void {
  config = {
    cacheTtlMs: Math.max(options.cacheTtlMs ?? DEFAULT_CONFIG.cacheTtlMs, MIN_TTL_MS),
  };
}

function getClient(region: string): SecretsManagerClient {
  let client = clients.get(region);
  if (!client) {
    client = new SecretsManagerClient({ region });
    clients.set(region, client);
  }
  return client;
}

/**
 * Retrieve a secret from AWS Secrets Manager with in-memory caching.
 *
 * On cache hit within the TTL window, returns the cached value without
 * making a Secrets Manager API call. After TTL expiry, fetches a fresh value.
 *
 * Error messages include the secret ARN but never expose the secret value.
 *
 * @param secretArn - The Secrets Manager secret ARN
 * @param region - Region containing the secret (may differ from the caller's own region)
 * @returns The decrypted secret string
 * @throws Error if the secret cannot be retrieved
 */
export async function getSecretFromSecretsManager(secretArn: string, region: string): Promise<string> {
  const now = Date.now();
  const cacheKey = `${region}:${secretArn}`;
  const cached = cache.get(cacheKey);

  if (cached && (now - cached.fetchedAt) < config.cacheTtlMs) {
    return cached.value;
  }

  try {
    const response = await getClient(region).send(
      new GetSecretValueCommand({ SecretId: secretArn })
    );

    const value = response.SecretString;
    if (value === undefined || value === null) {
      throw new Error(`Secrets Manager secret "${secretArn}" returned no string value`);
    }

    cache.set(cacheKey, { value, fetchedAt: now });
    return value;
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes(secretArn)) {
      throw error;
    }
    throw new Error(`Failed to retrieve Secrets Manager secret "${secretArn}": ${message}`);
  }
}

/**
 * Clear the secrets cache. Useful for testing or forced refresh.
 */
export function clearSecretsManagerCache(): void {
  cache.clear();
}

/**
 * Reset the secrets cache module entirely (cache + config + clients).
 * Primarily used in tests.
 */
export function resetSecretsManagerCache(): void {
  cache.clear();
  clients.clear();
  config = { ...DEFAULT_CONFIG };
}

/**
 * Inject a custom Secrets Manager client for a given region (for testing).
 */
export function setSecretsManagerClient(region: string, client: SecretsManagerClient): void {
  clients.set(region, client);
}
