// API service for AWS Services Lifecycle Tracker Admin UI
import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from '@aws-sdk/client-bedrock-agentcore';
import { CognitoIdentityClient } from '@aws-sdk/client-cognito-identity';
import { fromCognitoIdentityPool } from '@aws-sdk/credential-provider-cognito-identity';
import { getIdToken } from './auth';

const region = (import.meta as any).env?.VITE_REGION || 'us-east-1';
const agentRuntimeArn = (import.meta as any).env?.VITE_AGENT_RUNTIME_ARN;
const identityPoolId = (import.meta as any).env?.VITE_IDENTITY_POOL_ID;
const userPoolId = (import.meta as any).env?.VITE_USER_POOL_ID;

// Types
export interface ServiceConfig {
  service_name: string;
  name: string;
  enabled: boolean;
  documentation_urls: string[];
  extraction_focus: string;
  schema_key: string;
  item_properties: Record<string, string>;
  required_fields?: string[];
  last_extraction: string;
  extraction_count: number;
  success_rate: number;
  last_refresh_origin?: string;
  last_extraction_duration?: number;  // Duration in seconds
}

export interface DeprecationItem {
  service_name: string;
  item_id: string;
  status: 'deprecated' | 'extended_support' | 'end_of_life';
  source_url: string;
  extraction_date: string;
  last_verified: string;
  service_specific: Record<string, any>;
}

export interface DashboardMetrics {
  total_services: number;
  enabled_services: number;
  total_items: number;
  by_status: {
    deprecated: number;
    extended_support: number;
    end_of_life: number;
  };
  by_service: Record<string, number>;  // Add per-service item counts
  recent_extractions: Array<{
    service_name: string;
    timestamp: string;
    success: boolean;
  }>;
}


// Helper to get AWS credentials from Cognito Identity Pool
const getAwsCredentials = async () => {
  const idToken = await getIdToken();
  if (!idToken) {
    throw new Error('Not authenticated - no ID token available');
  }

  return fromCognitoIdentityPool({
    client: new CognitoIdentityClient({ region }),
    identityPoolId,
    logins: {
      [`cognito-idp.${region}.amazonaws.com/${userPoolId}`]: idToken,
    },
  });
};

// Helper to invoke agent with payload using AWS SDK
const invokeAgentWithPayload = async (payload: any): Promise<any> => {
  try {
    // Get AWS credentials from Cognito Identity Pool
    const credentials = await getAwsCredentials();

    // Create AgentCore client with IAM authentication
    const client = new BedrockAgentCoreClient({ 
      region, 
      credentials 
    });
    
    console.log('Invoking AgentCore with IAM authentication:', { agentRuntimeArn, region });
    console.log('Request payload:', payload);
    
    // Call AgentCore using AWS SDK
    const command = new InvokeAgentRuntimeCommand({
      agentRuntimeArn,
      payload: JSON.stringify(payload),
    });

    const response = await client.send(command);
    
    console.log('AgentCore response:', response);

    // Parse response (handle both new ReadableStream format and legacy payload format)
    const responseStream = response.response || response.payload;
    
    if (responseStream) {
      try {
        let payloadString: string;
        
        // Check if it's a ReadableStream with AWS SDK transform methods
        if (responseStream instanceof ReadableStream && typeof responseStream.transformToString === 'function') {
          // Use AWS SDK built-in transformation method
          payloadString = await responseStream.transformToString();
        } else if (responseStream instanceof ReadableStream) {
          // Fallback to manual stream reading
          const reader = responseStream.getReader();
          const chunks: Uint8Array[] = [];
          
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            chunks.push(value);
          }
          
          // Combine all chunks
          const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
          const combined = new Uint8Array(totalLength);
          let offset = 0;
          for (const chunk of chunks) {
            combined.set(chunk, offset);
            offset += chunk.length;
          }
          
          payloadString = new TextDecoder().decode(combined);
        } else {
          // Handle Uint8Array (legacy format)
          payloadString = new TextDecoder().decode(responseStream);
        }
        
        console.log('Parsed payload string:', payloadString);
        return JSON.parse(payloadString);
      } catch (parseError) {
        console.error('Failed to parse response payload:', parseError);
        return { response: 'Failed to parse agent response' };
      }
    } else {
      return { response: 'No response from agent' };
    }

  } catch (error: any) {
    console.error('AgentCore invocation error:', error);
    throw new Error(`Failed to invoke agent: ${error.message}`);
  }
};

// API Functions

export const getServices = async (): Promise<ServiceConfig[]> => {
  // Call agent to get services from DynamoDB
  const result = await invokeAgentWithPayload({
    action: 'list_services'
  });

  return result.services || [];
};

export const getDeprecations = async (filters?: {
  service?: string;
  status?: string;
  limit?: number;
}): Promise<DeprecationItem[]> => {
  const result = await invokeAgentWithPayload({
    action: 'list_deprecations',
    filters
  });

  return result.items || [];
};

export const triggerExtraction = async (serviceNames: string | string[]): Promise<any> => {
  const services = Array.isArray(serviceNames) ? serviceNames : [serviceNames];

  // If 'all', get all service names first
  let servicesToExtract = services;
  if (services.length === 1 && services[0] === 'all') {
    const allServices = await getServices();
    servicesToExtract = allServices.filter(s => s.enabled).map(s => s.service_name);
  }

  // Extract each service individually (agent expects service_name not services)
  const results = [];
  for (const serviceName of servicesToExtract) {
    try {
      const result = await invokeAgentWithPayload({
        service_name: serviceName,
        force_refresh: true,
        refresh_origin: 'manual'
      });
      results.push({ service: serviceName, success: true, result });
    } catch (error: any) {
      results.push({ service: serviceName, success: false, error: error.message });
    }
  }

  return {
    total: servicesToExtract.length,
    results
  };
};

export const getDashboardMetrics = async (): Promise<DashboardMetrics> => {
  const result = await invokeAgentWithPayload({
    action: 'get_metrics'
  });

  return result.metrics || {
    total_services: 0,
    enabled_services: 0,
    total_items: 0,
    by_status: { deprecated: 0, extended_support: 0, end_of_life: 0 },
    recent_extractions: []
  };
};

export const updateServiceConfig = async (serviceName: string, updates: Partial<ServiceConfig>): Promise<void> => {
  await invokeAgentWithPayload({
    action: 'update_service',
    service_name: serviceName,
    updates
  });
};

export const discoverAccountResources = async (options?: {
  region?: string;
  include_supported?: boolean;
}): Promise<{
  success: boolean;
  items_discovered?: number;
  items_saved?: number;
  summary?: {
    total: number;
    end_of_life: number;
    deprecated: number;
    supported: number;
    needs_attention: number;
  };
  error?: string;
}> => {
  const result = await invokeAgentWithPayload({
    action: 'discover_account',
    region: options?.region,
    include_supported: options?.include_supported ?? true
  });
  return result;
};

// Action Plan Types
export interface ActionPlan {
  plan_id: string;
  service_name: string;
  item_id: string;
  item_name: string;
  owner: string;
  plan_status: 'not_started' | 'in_progress' | 'completed' | 'blocked';
  priority: 'low' | 'medium' | 'high' | 'critical';
  target_date: string;
  notes: string;
  created_at: string;
  updated_at: string;
  created_by: string;
}

// Action Plan API Functions
export const getActionPlans = async (filters?: {
  owner?: string;
  plan_status?: string;
}): Promise<ActionPlan[]> => {
  const result = await invokeAgentWithPayload({
    action: 'list_action_plans',
    filters
  });
  return result.plans || [];
};

export const getActionPlan = async (planId: string): Promise<ActionPlan | null> => {
  const result = await invokeAgentWithPayload({
    action: 'get_action_plan',
    plan_id: planId
  });
  return result.plan || null;
};

export const createActionPlan = async (data: {
  service_name: string;
  item_id: string;
  item_name?: string;
  owner: string;
  plan_status?: string;
  priority?: string;
  target_date?: string;
  notes?: string;
}): Promise<{ success: boolean; plan?: ActionPlan; error?: string }> => {
  return await invokeAgentWithPayload({
    action: 'create_action_plan',
    ...data
  });
};

export const updateActionPlan = async (
  planId: string,
  updates: Partial<ActionPlan>
): Promise<{ success: boolean; plan?: ActionPlan; error?: string }> => {
  return await invokeAgentWithPayload({
    action: 'update_action_plan',
    plan_id: planId,
    updates
  });
};

export const deleteActionPlan = async (
  planId: string
): Promise<{ success: boolean; error?: string }> => {
  return await invokeAgentWithPayload({
    action: 'delete_action_plan',
    plan_id: planId
  });
};