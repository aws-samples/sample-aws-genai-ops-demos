/**
 * AgentCore Client — Authenticated access via Cognito Identity Pool
 *
 * Flow:
 * 1. User signs in → gets Cognito ID token
 * 2. GetId with Cognito login → Identity ID
 * 3. GetCredentialsForIdentity → temporary AWS credentials
 * 4. InvokeAgentRuntimeCommand with SigV4-signed request
 */

import {
  BedrockAgentCoreClient,
  InvokeAgentRuntimeCommand,
} from '@aws-sdk/client-bedrock-agentcore';
import {
  CognitoIdentityClient,
  GetIdCommand,
  GetCredentialsForIdentityCommand,
} from '@aws-sdk/client-cognito-identity';
import {
  CognitoIdentityProviderClient,
  InitiateAuthCommand,
  AuthFlowType,
} from '@aws-sdk/client-cognito-identity-provider';

const region = import.meta.env.VITE_REGION || 'us-east-1';
const agentRuntimeArn = import.meta.env.VITE_AGENT_RUNTIME_ARN;
const identityPoolId = import.meta.env.VITE_IDENTITY_POOL_ID;
const userPoolId = import.meta.env.VITE_USER_POOL_ID;
const userPoolClientId = import.meta.env.VITE_USER_POOL_CLIENT_ID;

export interface InvokeAgentRequest {
  prompt: string;
  idToken: string;
  sessionId?: string;
  accountContext?: string;
  onChunk?: (chunk: string) => void;
}

export interface InvokeAgentResponse {
  response: string;
  sessionId?: string;
}

interface AWSCredentials {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken: string;
  expiration?: Date;
}

let cachedCredentials: AWSCredentials | null = null;
let credentialsExpiry: Date | null = null;
let cachedIdToken: string | null = null;

/**
 * Check if a JWT token is expired (with 60s buffer).
 */
function isTokenExpired(token: string): boolean {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return true;
    const payload = JSON.parse(atob(parts[1]));
    if (typeof payload.exp !== 'number') return true;
    return payload.exp < Math.floor(Date.now() / 1000) + 60; // 60s buffer
  } catch {
    return true;
  }
}

/**
 * Refresh the Cognito ID token using the stored refresh token.
 * Returns a fresh ID token or null if refresh fails.
 */
async function refreshIdToken(): Promise<string | null> {
  const stored = sessionStorage.getItem('goat_user');
  if (!stored) return null;

  try {
    const { username, refreshToken } = JSON.parse(stored);
    if (!refreshToken) return null;

    const client = new CognitoIdentityProviderClient({ region });
    const response = await client.send(
      new InitiateAuthCommand({
        AuthFlow: AuthFlowType.REFRESH_TOKEN_AUTH,
        ClientId: userPoolClientId,
        AuthParameters: { REFRESH_TOKEN: refreshToken },
      }),
    );

    const newIdToken = response.AuthenticationResult?.IdToken;
    if (!newIdToken) return null;

    // REFRESH_TOKEN_AUTH does not return a new refresh token, so reuse
    // the existing one. Update only the ID token in the stored session.
    sessionStorage.setItem(
      'goat_user',
      JSON.stringify({ username, refreshToken, idToken: newIdToken }),
    );

    return newIdToken;
  } catch {
    return null;
  }
}

/**
 * Get a valid (non-expired) ID token, refreshing if needed.
 */
async function getValidIdToken(idToken: string): Promise<string> {
  if (!isTokenExpired(idToken)) return idToken;

  // Token expired — try to refresh
  const refreshed = await refreshIdToken();
  if (refreshed) return refreshed;

  // Clear cache and force re-sign-in
  cachedCredentials = null;
  cachedIdToken = null;
  throw new Error('Session expired. Please sign in again.');
}

/**
 * Get AWS credentials using Cognito Identity Pool enhanced (authenticated) flow.
 */
async function getAuthenticatedCredentials(idToken: string): Promise<AWSCredentials> {
  if (cachedCredentials && credentialsExpiry && cachedIdToken === idToken) {
    const now = new Date();
    if (credentialsExpiry.getTime() - now.getTime() > 5 * 60 * 1000) {
      return cachedCredentials;
    }
  }

  const cognitoClient = new CognitoIdentityClient({ region });
  const providerName = `cognito-idp.${region}.amazonaws.com/${userPoolId}`;

  const getIdResponse = await cognitoClient.send(
    new GetIdCommand({
      IdentityPoolId: identityPoolId,
      Logins: { [providerName]: idToken },
    }),
  );

  const identityId = getIdResponse.IdentityId;
  if (!identityId) throw new Error('Failed to get identity ID from Cognito');

  const credsResponse = await cognitoClient.send(
    new GetCredentialsForIdentityCommand({
      IdentityId: identityId,
      Logins: { [providerName]: idToken },
    }),
  );

  const creds = credsResponse.Credentials;
  if (!creds?.AccessKeyId || !creds?.SecretKey || !creds?.SessionToken) {
    throw new Error('Failed to get credentials from Cognito Identity');
  }

  cachedCredentials = {
    accessKeyId: creds.AccessKeyId,
    secretAccessKey: creds.SecretKey,
    sessionToken: creds.SessionToken,
    expiration: creds.Expiration,
  };
  credentialsExpiry = creds.Expiration ?? null;
  cachedIdToken = idToken;

  return cachedCredentials;
}

/**
 * Invoke the orchestration agent via AgentCore SDK.
 * Automatically refreshes expired tokens before calling.
 */
export async function invokeAgent(request: InvokeAgentRequest): Promise<InvokeAgentResponse> {
  if (!agentRuntimeArn) {
    throw new Error('AgentCore Runtime ARN not configured. Check deployment.');
  }
  if (!identityPoolId) {
    throw new Error('Identity Pool ID not configured. Check deployment.');
  }

  // Ensure we have a valid (non-expired) token
  const validToken = await getValidIdToken(request.idToken);
  const credentials = await getAuthenticatedCredentials(validToken);

  const client = new BedrockAgentCoreClient({
    region,
    credentials: {
      accessKeyId: credentials.accessKeyId,
      secretAccessKey: credentials.secretAccessKey,
      sessionToken: credentials.sessionToken,
    },
  });

  const payload: Record<string, unknown> = { prompt: request.prompt };
  if (request.accountContext) {
    payload.accountContext = request.accountContext;
  }
  // Extract Cognito groups from the ID token and pass to the agent
  // so the server-side capture authorization check works.
  try {
    const tokenParts = validToken.split('.');
    if (tokenParts.length === 3) {
      const tokenPayload = JSON.parse(atob(tokenParts[1]));
      const groups = tokenPayload['cognito:groups'];
      if (Array.isArray(groups) && groups.length > 0) {
        payload.user_groups = groups;
      }
    }
  } catch { /* ignore decode errors */ }

  const command = new InvokeAgentRuntimeCommand({
    agentRuntimeArn,
    runtimeSessionId: request.sessionId,
    payload: JSON.stringify(payload),
  });

  let response;
  try {
    response = await client.send(command);
  } catch (err: unknown) {
    const msg = (err as Error).message || '';
    // If token expired during the call, clear cache and retry once
    if (msg.includes('expired') || msg.includes('Token')) {
      cachedCredentials = null;
      cachedIdToken = null;
      const retryToken = await getValidIdToken(request.idToken);
      const retryCreds = await getAuthenticatedCredentials(retryToken);
      const retryClient = new BedrockAgentCoreClient({
        region,
        credentials: {
          accessKeyId: retryCreds.accessKeyId,
          secretAccessKey: retryCreds.secretAccessKey,
          sessionToken: retryCreds.sessionToken,
        },
      });
      response = await retryClient.send(command);
    } else {
      throw err;
    }
  }

  let responseText = '';

  if (response.response) {
    const raw = await response.response.transformToString();

    const lines = raw.split('\n');
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6).trim();
        if (data && data !== '[DONE]') {
          try {
            const parsed = JSON.parse(data);
            const text =
              typeof parsed === 'string'
                ? parsed
                : parsed.content || parsed.text || parsed.message || JSON.stringify(parsed);
            responseText += text;
          } catch {
            responseText += data;
          }
        }
      }
    }
    if (!responseText) {
      responseText = raw;
    }
  } else {
    responseText = 'No response from agent';
  }

  // Strip <thinking>...</thinking> blocks from the response
  const cleaned = responseText.replace(/<thinking>[\s\S]*?<\/thinking>\s*/g, '').trim();
  request.onChunk?.(cleaned);

  // Capture the session ID from the response for conversation continuity
  const responseSessionId = response.runtimeSessionId;

  return { response: cleaned, sessionId: responseSessionId };
}
