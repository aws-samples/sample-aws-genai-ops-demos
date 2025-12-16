/**
 * AgentCore Client - Anonymous Access via Basic/Classic Auth Flow
 * 
 * Uses Cognito Identity Pool's BASIC (classic) auth flow to avoid session policy restrictions.
 * The enhanced flow applies restrictive session policies that block bedrock-agentcore actions.
 * 
 * Basic Flow:
 * 1. GetId - Get identity ID from Cognito Identity Pool
 * 2. GetOpenIdToken - Get OIDC token for the identity
 * 3. AssumeRoleWithWebIdentity - Exchange OIDC token for AWS credentials (no session policy)
 * 4. Use credentials with AWS SDK (SDK handles SigV4 signing internally)
 */

import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from '@aws-sdk/client-bedrock-agentcore';
import { CognitoIdentityClient, GetIdCommand, GetOpenIdTokenCommand } from '@aws-sdk/client-cognito-identity';
import { STSClient, AssumeRoleWithWebIdentityCommand } from '@aws-sdk/client-sts';

const region = (import.meta as any).env.VITE_REGION || 'us-east-1';
const agentRuntimeArn = (import.meta as any).env.VITE_AGENT_RUNTIME_ARN;
const identityPoolId = (import.meta as any).env.VITE_IDENTITY_POOL_ID;
const unauthRoleArn = (import.meta as any).env.VITE_UNAUTH_ROLE_ARN;
const isLocalDev = (import.meta as any).env.VITE_LOCAL_DEV === 'true';
const localAgentUrl = (import.meta as any).env.VITE_AGENT_RUNTIME_URL || '/api';

export interface InvokeAgentRequest {
  prompt: string;
  sessionId?: string;
  onChunk?: (chunk: string) => void;
}

export interface InvokeAgentResponse {
  response: string;
}

interface AWSCredentials {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken: string;
  expiration?: Date;
}

// Cache credentials to avoid repeated auth calls
let cachedCredentials: AWSCredentials | null = null;
let credentialsExpiry: Date | null = null;

/**
 * Get AWS credentials using Cognito Identity Pool's BASIC (classic) auth flow.
 * This bypasses the session policy restrictions of the enhanced flow.
 */
async function getBasicFlowCredentials(): Promise<AWSCredentials> {
  // Return cached credentials if still valid (with 5 min buffer)
  if (cachedCredentials && credentialsExpiry) {
    const now = new Date();
    const bufferMs = 5 * 60 * 1000;
    if (credentialsExpiry.getTime() - now.getTime() > bufferMs) {
      return cachedCredentials;
    }
  }

  const cognitoClient = new CognitoIdentityClient({ region });
  
  // Step 1: Get identity ID
  const getIdResponse = await cognitoClient.send(new GetIdCommand({
    IdentityPoolId: identityPoolId,
  }));
  
  const identityId = getIdResponse.IdentityId;
  if (!identityId) {
    throw new Error('Failed to get identity ID from Cognito');
  }
  console.log('Got identity ID:', identityId);

  // Step 2: Get OpenID token (basic/classic flow)
  const getTokenResponse = await cognitoClient.send(new GetOpenIdTokenCommand({
    IdentityId: identityId,
  }));
  
  const oidcToken = getTokenResponse.Token;
  if (!oidcToken) {
    throw new Error('Failed to get OpenID token from Cognito');
  }
  console.log('Got OpenID token');

  // Step 3: Exchange OIDC token for AWS credentials via STS (no session policy applied)
  const stsClient = new STSClient({ region });
  const assumeRoleResponse = await stsClient.send(new AssumeRoleWithWebIdentityCommand({
    RoleArn: unauthRoleArn,
    RoleSessionName: `password-reset-${Date.now()}`,
    WebIdentityToken: oidcToken,
  }));

  const creds = assumeRoleResponse.Credentials;
  if (!creds?.AccessKeyId || !creds?.SecretAccessKey || !creds?.SessionToken) {
    throw new Error('Failed to get credentials from STS');
  }
  console.log('Got AWS credentials via basic auth flow');

  cachedCredentials = {
    accessKeyId: creds.AccessKeyId,
    secretAccessKey: creds.SecretAccessKey,
    sessionToken: creds.SessionToken,
    expiration: creds.Expiration,
  };
  credentialsExpiry = creds.Expiration || null;

  return cachedCredentials;
}


export const invokeAgent = async (request: InvokeAgentRequest): Promise<InvokeAgentResponse> => {
  try {
    // Local development mode
    if (isLocalDev) {
      console.log('Invoking local AgentCore:', { url: localAgentUrl });
      
      const response = await fetch(`${localAgentUrl}/invocations`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          ...(request.sessionId && { 'X-Session-Id': request.sessionId })
        },
        body: JSON.stringify({ 
          prompt: request.prompt
        }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Local AgentCore error: ${response.status} - ${errorText}`);
      }

      return handleStreamingResponse(response, request.onChunk);
    }

    // Production mode - call AWS AgentCore with credentials from basic auth flow
    if (!agentRuntimeArn) {
      throw new Error('AgentCore Runtime ARN not configured. Please check deployment.');
    }
    if (!identityPoolId) {
      throw new Error('Identity Pool ID not configured. Please check deployment.');
    }
    if (!unauthRoleArn) {
      throw new Error('Unauthenticated Role ARN not configured. Please check deployment.');
    }

    console.log('Getting AWS credentials via basic auth flow...');
    const credentials = await getBasicFlowCredentials();

    // Create AgentCore client with the credentials from basic auth flow
    // SDK handles SigV4 signing internally
    const client = new BedrockAgentCoreClient({ 
      region, 
      credentials: {
        accessKeyId: credentials.accessKeyId,
        secretAccessKey: credentials.secretAccessKey,
        sessionToken: credentials.sessionToken,
      },
    });
    
    console.log('Invoking AgentCore:', { agentRuntimeArn, region });
    
    const command = new InvokeAgentRuntimeCommand({
      agentRuntimeArn,
      mcpSessionId: request.sessionId,
      payload: JSON.stringify({
        prompt: request.prompt
      }),
    });

    const response = await client.send(command);
    console.log('AgentCore response:', response);

    // Parse response - SDK returns response.response as a stream
    let responseText = '';
    if (response.response) {
      // Transform stream to string
      const payloadString = await response.response.transformToString();
      console.log('Raw payload:', payloadString);
      
      // Handle SSE streaming format (data: lines)
      const lines = payloadString.split('\n');
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6).trim();
          if (data) {
            try {
              const parsed = JSON.parse(data);
              const textContent = typeof parsed === 'string' ? parsed : 
                                parsed.content || parsed.text || parsed.message || 
                                JSON.stringify(parsed);
              responseText += textContent;
              if (request.onChunk) request.onChunk(textContent);
            } catch {
              responseText += data;
              if (request.onChunk) request.onChunk(data);
            }
          }
        }
      }
      // If no SSE format, use raw response
      if (!responseText) {
        responseText = payloadString;
      }
    } else {
      responseText = 'No response from agent';
    }
    
    return { response: responseText };

  } catch (error: any) {
    console.error('AgentCore invocation error:', error);
    throw new Error(`Failed to invoke agent: ${error.message}`);
  }
};


/**
 * Handle streaming SSE response from AgentCore (for local dev mode)
 */
async function handleStreamingResponse(
  response: Response,
  onChunk?: (chunk: string) => void
): Promise<InvokeAgentResponse> {
  if (onChunk && response.body) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let fullResponse = '';
    let buffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            try {
              const parsed = JSON.parse(data);
              const textContent = typeof parsed === 'string' ? parsed : 
                                parsed.content || parsed.text || parsed.message || 
                                JSON.stringify(parsed);
              fullResponse += textContent;
              onChunk(textContent);
            } catch {
              fullResponse += data;
              onChunk(data);
            }
          }
        }
      }
      return { response: fullResponse };
    } finally {
      reader.releaseLock();
    }
  }

  const text = await response.text();
  try {
    const data = JSON.parse(text);
    const responseText = typeof data === 'string' ? data :
      data.response || data.content || data.text || data.message || data.output || JSON.stringify(data);
    return { response: responseText };
  } catch {
    return { response: text };
  }
}
