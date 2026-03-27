/**
 * AgentCore Direct Invocation Module
 *
 * Calls the three AgentCore Runtimes directly via SigV4 using AWS SDK.
 * Authentication: Cognito User Pool → ID Token → Cognito Identity Pool → AWS Credentials → AgentCore (IAM)
 */

import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from '@aws-sdk/client-bedrock-agentcore';
import { CognitoIdentityClient } from '@aws-sdk/client-cognito-identity';
import { fromCognitoIdentityPool } from '@aws-sdk/credential-provider-cognito-identity';
import { getIdToken } from './auth';

const region = import.meta.env.VITE_REGION || 'us-east-1';
const discoverRuntimeArn = import.meta.env.VITE_DISCOVER_RUNTIME_ARN;
const analyzeRuntimeArn = import.meta.env.VITE_ANALYZE_RUNTIME_ARN;
const transformRuntimeArn = import.meta.env.VITE_TRANSFORM_RUNTIME_ARN;
const identityPoolId = import.meta.env.VITE_IDENTITY_POOL_ID;
const userPoolId = import.meta.env.VITE_USER_POOL_ID;

async function getCredentials() {
  const idToken = await getIdToken();
  if (!idToken) throw new Error('Not authenticated — no ID token available');

  return fromCognitoIdentityPool({
    client: new CognitoIdentityClient({ region }),
    identityPoolId,
    logins: {
      [`cognito-idp.${region}.amazonaws.com/${userPoolId}`]: idToken,
    },
  });
}

async function invokeRuntime(runtimeArn: string, payload: Record<string, unknown>): Promise<unknown> {
  if (!runtimeArn) throw new Error('Runtime ARN not configured. Check deployment.');
  if (!identityPoolId) throw new Error('Identity Pool ID not configured.');
  if (!userPoolId) throw new Error('User Pool ID not configured.');

  const credentials = await getCredentials();
  const client = new BedrockAgentCoreClient({ region, credentials });

  const command = new InvokeAgentRuntimeCommand({
    agentRuntimeArn: runtimeArn,
    payload: JSON.stringify(payload),
  });

  const response = await client.send(command);

  // Parse response (handle both ReadableStream and legacy Uint8Array payload formats)
  // The SDK may return data in response.response (ReadableStream) or response.payload (Uint8Array)
  const responseStream = (response as any).response || response.payload;

  if (responseStream) {
    let payloadString: string;

    if (responseStream instanceof ReadableStream && typeof (responseStream as any).transformToString === 'function') {
      // AWS SDK built-in transformation method
      payloadString = await (responseStream as any).transformToString();
    } else if (responseStream instanceof ReadableStream) {
      // Manual stream reading fallback
      const reader = responseStream.getReader();
      const chunks: Uint8Array[] = [];
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
      }
      const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
      const combined = new Uint8Array(totalLength);
      let offset = 0;
      for (const chunk of chunks) {
        combined.set(chunk, offset);
        offset += chunk.length;
      }
      payloadString = new TextDecoder().decode(combined);
    } else {
      // Uint8Array (legacy format)
      payloadString = new TextDecoder().decode(responseStream);
    }

    try {
      return JSON.parse(payloadString);
    } catch {
      return payloadString;
    }
  }

  return null;
}


/**
 * Phase 1: Discover — triggers full discovery + enrichment + prioritization.
 * Returns the complete inventory with priority scores.
 */
export async function invokeDiscover(): Promise<unknown> {
  return invokeRuntime(discoverRuntimeArn, { action: 'discover' });
}

/**
 * Read existing inventory from DynamoDB (fast, no TA/Lambda API calls).
 * Used by Functions page and other views that just need current data.
 */
export async function readInventory(): Promise<unknown> {
  return invokeRuntime(discoverRuntimeArn, { action: 'read_inventory' });
}

/**
 * Phase 2: Analyze — downloads code, runs analysis, classifies complexity.
 */
export async function invokeAnalyze(functionArn: string): Promise<unknown> {
  return invokeRuntime(analyzeRuntimeArn, { function_arn: functionArn });
}

/**
 * Phase 3: Transform — generates migrated code with validation loop.
 */
export async function invokeTransform(functionArn: string): Promise<unknown> {
  return invokeRuntime(transformRuntimeArn, { function_arn: functionArn });
}
