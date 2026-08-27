import { EventBridgeEvent, Context } from 'aws-lambda';
import { SFNClient, StartExecutionCommand } from '@aws-sdk/client-sfn';

const sfnClient = new SFNClient({});
const STATE_MACHINE_ARN = process.env.STATE_MACHINE_ARN!;

interface HealthEventDetail {
  eventArn: string;
  service: string;
  eventTypeCode: string;
  eventTypeCategory: string;
  region: string;
  availabilityZone?: string;
  startTime?: string;
  endTime?: string;
  lastUpdatedTime?: string;
  statusCode?: string;
  eventScopeCode?: string;
  eventDescription?: Array<{ language: string; latestDescription: string }>;
  affectedEntities?: Array<{
    entityValue: string;
    tags?: Record<string, string>;
    status?: string;
  }>;
}

export const handler = async (
  event: EventBridgeEvent<'AWS Health Event', HealthEventDetail>,
  context: Context
): Promise<{ statusCode: number; executionArn: string }> => {
  console.log('Received Health event:', JSON.stringify(event, null, 2));

  const detail = event.detail;

  // Normalize the event into a structured payload for the workflow
  const workflowInput = {
    eventId: detail.eventArn,
    service: detail.service,
    eventType: detail.eventTypeCode,
    category: detail.eventTypeCategory,
    region: event.region || detail.region,
    availabilityZone: detail.availabilityZone || null,
    startTime: detail.startTime || null,
    endTime: detail.endTime || null,
    status: detail.statusCode || 'unknown',
    description: extractDescription(detail.eventDescription),
    affectedResources: (detail.affectedEntities || []).map(entity => ({
      resourceId: entity.entityValue,
      tags: entity.tags || {},
      status: entity.status || 'unknown',
    })),
    sourceAccountId: event.account,
    rawEvent: event,
    ingestedAt: new Date().toISOString(),
  };

  // Start the Step Functions execution
  const executionName = `health-${Date.now()}-${context.awsRequestId.slice(0, 8)}`;

  const command = new StartExecutionCommand({
    stateMachineArn: STATE_MACHINE_ARN,
    name: executionName,
    input: JSON.stringify(workflowInput),
  });

  const response = await sfnClient.send(command);

  console.log(`Started execution: ${response.executionArn}`);

  return {
    statusCode: 200,
    executionArn: response.executionArn!,
  };
};

function extractDescription(
  descriptions?: Array<{ language: string; latestDescription: string }>
): string {
  if (!descriptions || descriptions.length === 0) {
    return 'No description available';
  }

  // Prefer English description
  const english = descriptions.find(d => d.language === 'en_US' || d.language === 'en');
  return (english || descriptions[0]).latestDescription;
}
