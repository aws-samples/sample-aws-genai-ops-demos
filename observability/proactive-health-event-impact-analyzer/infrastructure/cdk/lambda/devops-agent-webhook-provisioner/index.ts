import {
  AssociateServiceCommand,
  DevOpsAgentClient,
  DisassociateServiceCommand,
  ListAssociationsCommand,
  ListServicesCommand,
  RegisterServiceCommand,
} from '@aws-sdk/client-devops-agent';
import {
  PutSecretValueCommand,
  SecretsManagerClient,
} from '@aws-sdk/client-secrets-manager';

/**
 * Lambda-backed CloudFormation custom resource that provisions a DevOps Agent
 * eventChannel webhook.
 *
 * Why a custom resource is unavoidable:
 * - AWS::DevOpsAgent::Service cannot register the eventChannel service type.
 * - AWS::DevOpsAgent::Association exposes no webhook URL or HMAC secret.
 * - AssociateService returns the secret exactly once, in its create response.
 *
 * The secret is written directly to Secrets Manager and is NEVER returned to
 * CloudFormation. Data contains only WebhookUrl; PhysicalResourceId is the
 * association id.
 */

interface CustomResourceEvent {
  RequestType: 'Create' | 'Update' | 'Delete';
  ResponseURL: string;
  StackId: string;
  RequestId: string;
  ResourceType: string;
  LogicalResourceId: string;
  PhysicalResourceId?: string;
  ResourceProperties: {
    ServiceToken: string;
    AgentSpaceId: string;
    SecretArn: string;
  };
}

interface LambdaContext {
  logStreamName: string;
}

const devOpsAgent = new DevOpsAgentClient({});
const secretsManager = new SecretsManagerClient({});
const SERVICE_TYPE = 'eventChannel' as const;

class WebhookProvisioningError extends Error {
  constructor(message: string, readonly associationId: string) {
    super(message);
    this.name = 'WebhookProvisioningError';
  }
}

export async function handler(event: CustomResourceEvent, context: LambdaContext): Promise<void> {
  // ResponseURL is a bearer URL — never log it.
  const { ResponseURL: _responseUrl, ...safeEvent } = event;
  console.info('Request', JSON.stringify(safeEvent));

  let physicalResourceId = event.PhysicalResourceId ?? 'failed';
  try {
    if (event.RequestType === 'Create' || event.RequestType === 'Update') {
      const result = await createWebhook(
        event.ResourceProperties.AgentSpaceId,
        event.ResourceProperties.SecretArn,
      );
      physicalResourceId = result.associationId;
      await respond(event, context, 'SUCCESS', physicalResourceId, {
        WebhookUrl: result.webhookUrl,
      });
      return;
    }

    await deleteWebhook(
      event.ResourceProperties.AgentSpaceId,
      event.PhysicalResourceId,
    );
    await respond(event, context, 'SUCCESS', event.PhysicalResourceId ?? 'deleted');
  } catch (error) {
    if (error instanceof WebhookProvisioningError) {
      // Compensation failed, so preserve the live association id. A subsequent
      // CloudFormation rollback DELETE can retry disassociating it.
      physicalResourceId = error.associationId;
    }
    console.error('Webhook provisioning failed', error);
    await respond(
      event,
      context,
      'FAILED',
      physicalResourceId,
      undefined,
      error instanceof Error ? `${error.name}: ${error.message}` : String(error),
    );
  }
}

async function createWebhook(
  agentSpaceId: string,
  secretArn: string,
): Promise<{ associationId: string; webhookUrl: string }> {
  const serviceId = await ensureEventChannelService();

  // The create response is the one and only carrier of webhookSecret.
  const association = await devOpsAgent.send(new AssociateServiceCommand({
    agentSpaceId,
    serviceId,
    configuration: { eventChannel: {} },
  }));

  let associationId = association.association?.associationId;
  const webhookUrl = association.webhook?.webhookUrl;
  const webhookSecret = association.webhook?.webhookSecret;

  // The modeled response includes association.associationId, but recover it by
  // service id if the service returns a partial response. We need the id both for
  // normal ownership and for compensating rollback.
  if (!associationId) {
    const existing = await devOpsAgent.send(new ListAssociationsCommand({ agentSpaceId }));
    associationId = existing.associations?.find((item) => item.serviceId === serviceId)?.associationId;
  }

  if (!associationId) {
    throw new Error('AssociateService succeeded but no association id could be recovered');
  }

  try {
    if (!webhookUrl || !webhookSecret) {
      throw new Error(
        'AssociateService did not return complete webhook credentials '
        + `(url=${webhookUrl ? 'present' : 'MISSING'}, `
        + `secret=${webhookSecret ? 'present' : 'MISSING'})`,
      );
    }

    // Persist before returning anything to CloudFormation. The value never enters
    // CFN state, events, outputs, or Lambda environment variables.
    await secretsManager.send(new PutSecretValueCommand({
      SecretId: secretArn,
      SecretString: webhookSecret,
    }));
  } catch (error) {
    // AssociateService already created a live webhook whose secret cannot be
    // recovered later. Compensate immediately. If compensation itself fails,
    // preserve the association id in the thrown error so CFN rollback can retry.
    try {
      await disassociate(agentSpaceId, associationId);
      console.info('Compensated partial webhook creation', { associationId });
    } catch (cleanupError) {
      throw new WebhookProvisioningError(
        `Webhook setup failed and compensating disassociation also failed: ${formatError(cleanupError)}`,
        associationId,
      );
    }
    throw error;
  }

  console.info('Webhook created; secret stored', { associationId });
  return { associationId, webhookUrl };
}

async function ensureEventChannelService(): Promise<string> {
  try {
    const registered = await devOpsAgent.send(new RegisterServiceCommand({
      service: SERVICE_TYPE,
      serviceDetails: { eventChannel: {} },
    }));
    if (registered.serviceId) {
      console.info('Registered eventChannel service', { serviceId: registered.serviceId });
      return registered.serviceId;
    }
  } catch (error) {
    // Registration is account-level and may already exist from a previous run.
    console.info('RegisterService failed; looking for an existing eventChannel registration', {
      error: error instanceof Error ? error.message : String(error),
    });
  }

  let nextToken: string | undefined;
  do {
    const page = await devOpsAgent.send(new ListServicesCommand({
      filterServiceType: SERVICE_TYPE,
      nextToken,
    }));
    const match = page.services?.find((service) => service.serviceType === SERVICE_TYPE);
    if (match?.serviceId) {
      console.info('Reusing existing eventChannel service', { serviceId: match.serviceId });
      return match.serviceId;
    }
    nextToken = page.nextToken;
  } while (nextToken);

  throw new Error('Could not register or locate the account-level eventChannel service');
}

async function deleteWebhook(
  agentSpaceId: string | undefined,
  associationId: string | undefined,
): Promise<void> {
  if (!agentSpaceId || !associationId || ['failed', 'deleted', 'unknown'].includes(associationId)) {
    console.info('Nothing to delete', { agentSpaceId, associationId });
    return;
  }

  try {
    await disassociate(agentSpaceId, associationId);
    console.info('Webhook association deleted', { associationId });
  } catch (error) {
    if (isNotFound(error)) {
      console.info('Webhook association already absent', { associationId });
      return;
    }
    // Access denial, throttling, service outage, and malformed requests must fail
    // closed so CloudFormation keeps ownership and can retry the deletion.
    throw error;
  }
}

async function disassociate(agentSpaceId: string, associationId: string): Promise<void> {
  await devOpsAgent.send(new DisassociateServiceCommand({ agentSpaceId, associationId }));
}

function isNotFound(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const candidate = error as { name?: string; $metadata?: { httpStatusCode?: number } };
  return candidate.name === 'ResourceNotFoundException'
    || candidate.name === 'NotFoundException'
    || candidate.$metadata?.httpStatusCode === 404;
}

function formatError(error: unknown): string {
  return error instanceof Error ? `${error.name}: ${error.message}` : String(error);
}

async function respond(
  event: CustomResourceEvent,
  context: LambdaContext,
  status: 'SUCCESS' | 'FAILED',
  physicalResourceId: string,
  data: Record<string, string> = {},
  reason?: string,
): Promise<void> {
  const body = JSON.stringify({
    Status: status,
    Reason: reason ?? `See CloudWatch log stream ${context.logStreamName}`,
    PhysicalResourceId: physicalResourceId,
    StackId: event.StackId,
    RequestId: event.RequestId,
    LogicalResourceId: event.LogicalResourceId,
    Data: data,
  });

  const response = await fetch(event.ResponseURL, {
    method: 'PUT',
    headers: {
      'content-type': '',
      'content-length': Buffer.byteLength(body).toString(),
    },
    body,
  });

  if (!response.ok) {
    throw new Error(`CloudFormation response upload failed: ${response.status} ${response.statusText}`);
  }
}
