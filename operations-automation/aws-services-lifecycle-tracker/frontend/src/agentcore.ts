// Using AWS SDK with IAM authentication via Cognito Identity Pool
import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from '@aws-sdk/client-bedrock-agentcore';
import { CognitoIdentityClient } from '@aws-sdk/client-cognito-identity';
import { fromCognitoIdentityPool } from '@aws-sdk/credential-provider-cognito-identity';
import { getIdToken } from './auth';

const region = import.meta.env.VITE_REGION || 'us-east-1';
const agentRuntimeArn = import.meta.env.VITE_AGENT_RUNTIME_ARN;
const identityPoolId = import.meta.env.VITE_IDENTITY_POOL_ID;
const userPoolId = import.meta.env.VITE_USER_POOL_ID;

export interface InvokeAgentRequest {
  prompt: string;
}

export interface InvokeAgentResponse {
  response: string;
}

export const invokeAgent = async (request: InvokeAgentRequest): Promise<InvokeAgentResponse> => {
  try {
    // Check if required configuration is available
    if (!agentRuntimeArn) {
      throw new Error('AgentCore Runtime ARN not configured. Please check deployment.');
    }
    if (!identityPoolId) {
      throw new Error('Identity Pool ID not configured. Please check deployment.');
    }
    if (!userPoolId) {
      throw new Error('User Pool ID not configured. Please check deployment.');
    }

    // Get JWT ID token from Cognito User Pool (required for Identity Pool)
    const idToken = await getIdToken();
    if (!idToken) {
      throw new Error('Not authenticated - no ID token available');
    }

    console.log('Getting AWS credentials via Cognito Identity Pool...');

    // Get AWS credentials from Cognito Identity Pool
    const credentials = fromCognitoIdentityPool({
      client: new CognitoIdentityClient({ region }),
      identityPoolId,
      logins: {
        [`cognito-idp.${region}.amazonaws.com/${userPoolId}`]: idToken,
      },
    });

    // Create AgentCore client with IAM authentication
    const client = new BedrockAgentCoreClient({ 
      region, 
      credentials 
    });
    
    console.log('Invoking AgentCore with IAM authentication:', { agentRuntimeArn, region });
    console.log('Request payload:', { prompt: request.prompt });
    
    // Call AgentCore using AWS SDK
    const command = new InvokeAgentRuntimeCommand({
      agentRuntimeArn,
      payload: JSON.stringify({
        prompt: request.prompt
      }),
    });

    const response = await client.send(command);
    
    console.log('AgentCore response:', response);

    // Parse response payload
    let responseText = '';
    if (response.payload) {
      try {
        const payloadString = new TextDecoder().decode(response.payload);
        const data = JSON.parse(payloadString);
        
        if (typeof data === 'string') {
          responseText = data;
        } else if (data && typeof data === 'object') {
          responseText = data.response || data.content || data.text || data.message || data.output || JSON.stringify(data);
        } else {
          responseText = payloadString;
        }
      } catch (parseError) {
        console.error('Failed to parse response payload:', parseError);
        responseText = new TextDecoder().decode(response.payload);
      }
    } else {
      responseText = 'No response from agent';
    }
    
    console.log('Final response text:', responseText);
    
    return {
      response: responseText
    };

  } catch (error: any) {
    console.error('AgentCore invocation error:', error);
    throw new Error(`Failed to invoke agent: ${error.message}`);
  }
};