import { SSMClient, CreateOpsItemCommand } from '@aws-sdk/client-ssm';

const ssmClient = new SSMClient({});
const AWS_REGION = process.env.AWS_REGION || 'us-east-1';

// ─── Interfaces ─────────────────────────────────────────────────────────────

interface Finding {
  description: string;
  severity: string;
  affectedResources: string[];
  owningTeam?: string;
}

interface Recommendation {
  description: string;
  priority: string;
}

interface InvestigationResult {
  investigationStatus: 'IMPACT_DETECTED' | 'NO_IMPACT';
  summary: string;
  rootCause: string | null;
  priority: string;
  findings: Finding[];
  recommendations: Recommendation[];
  teamsToNotify?: string[];
  investigationLink?: string | null;
}

interface HealthEventContext {
  eventId: string;
  service: string;
  eventType: string;
  category: string;
  region: string;
  availabilityZone: string | null;
  startTime: string | null;
  endTime: string | null;
  status: string;
  description: string;
  affectedResources: Array<{
    resourceId: string;
    tags: Record<string, string>;
    status: string;
  }>;
  sourceAccountId?: string;
  ingestedAt: string;
}

/**
 * Input from Step Functions.
 * The workflow preserves the original health event in the state
 * and the investigation result comes from the TriggerInvestigation step output.
 */
interface OpsItemCreatorInput {
  investigationResult: InvestigationResult;
  healthEvent: HealthEventContext;
}

interface OpsItemCreatorOutput {
  opsItemId: string;
  opsItemUrl: string;
  investigationResult: InvestigationResult;
}

// ─── Category Mapping ───────────────────────────────────────────────────────

/**
 * Maps Health event category to OpsItem Category.
 * ADR-8: Category mappata automaticamente da eventTypeCategory.
 */
function mapEventCategoryToOpsCategory(eventCategory: string, eventType: string): string {
  switch (eventCategory) {
    case 'issue':
      return 'Availability';
    case 'scheduledChange':
      return 'Availability';
    case 'accountNotification':
      // Abuse events are security-related
      if (eventType.toLowerCase().includes('abuse')) {
        return 'Security';
      }
      return 'Performance';
    default:
      return 'Availability';
  }
}

// ─── Severity Mapping ───────────────────────────────────────────────────────

/**
 * Maps DevOps Agent priority to OpsItem Severity (1-4 scale).
 * OpsCenter uses: 1=Critical, 2=High, 3=Medium, 4=Low
 */
function mapPriorityToSeverity(priority: string): string {
  const severityMap: Record<string, string> = {
    CRITICAL: '1',
    HIGH: '2',
    MEDIUM: '3',
    LOW: '4',
    MINIMAL: '4',
  };
  return severityMap[priority] || '3';
}

// ─── Description Builder ────────────────────────────────────────────────────

// SSM OpsItem description hard limit. The API rejects anything longer
// with a ValidationException. Source: AWS SSM CreateOpsItem reference.
const OPSITEM_DESCRIPTION_MAX = 2048;

/**
 * Truncate a description to fit the SSM OpsItem 2048-char limit. Reserves
 * a small footer that explains the truncation and tells the operator
 * where to find the full text (the agent investigation link is included
 * in OperationalData on the OpsItem).
 */
function truncateForOpsItem(description: string): string {
  if (description.length <= OPSITEM_DESCRIPTION_MAX) return description;
  const footer = '\n\n…[truncated to fit SSM OpsItem 2048-char limit; see full analysis in the agent investigation link in OperationalData]';
  const head = description.slice(0, OPSITEM_DESCRIPTION_MAX - footer.length);
  return head + footer;
}

/**
 * Builds a rich markdown description for the OpsItem.
 * Includes all relevant context from the investigation and the original event.
 */
function buildOpsItemDescription(
  investigation: InvestigationResult,
  healthEvent: HealthEventContext
): string {
  const lines: string[] = [];

  lines.push('## Summary');
  lines.push('');
  lines.push(investigation.summary);
  lines.push('');

  if (investigation.rootCause) {
    lines.push('## Root Cause');
    lines.push('');
    lines.push(investigation.rootCause);
    lines.push('');
  }

  lines.push('## Health Event Details');
  lines.push('');
  lines.push(`- **Service:** ${healthEvent.service}`);
  lines.push(`- **Event Type:** ${healthEvent.eventType}`);
  lines.push(`- **Category:** ${healthEvent.category}`);
  lines.push(`- **Region:** ${healthEvent.region}`);
  if (healthEvent.availabilityZone) {
    lines.push(`- **Availability Zone:** ${healthEvent.availabilityZone}`);
  }
  if (healthEvent.startTime && healthEvent.endTime) {
    lines.push(`- **Maintenance Window:** ${healthEvent.startTime} → ${healthEvent.endTime}`);
  }
  lines.push(`- **Status:** ${healthEvent.status}`);
  if (healthEvent.sourceAccountId) {
    lines.push(`- **Source Account:** ${healthEvent.sourceAccountId}`);
  }
  lines.push('');

  if (investigation.findings.length > 0) {
    lines.push('## Findings');
    lines.push('');
    for (const finding of investigation.findings) {
      lines.push(`### [${finding.severity}] ${finding.description}`);
      if (finding.affectedResources.length > 0) {
        lines.push(`- Resources: ${finding.affectedResources.join(', ')}`);
      }
      if (finding.owningTeam) {
        lines.push(`- Owning Team: ${finding.owningTeam}`);
      }
      lines.push('');
    }
  }

  if (investigation.recommendations.length > 0) {
    lines.push('## Recommendations');
    lines.push('');
    investigation.recommendations.forEach((rec, i) => {
      lines.push(`${i + 1}. **[${rec.priority}]** ${rec.description}`);
    });
    lines.push('');
  }

  lines.push('---');
  lines.push('*Assessment by AWS DevOps Agent using application topology.*');
  if (investigation.investigationLink) {
    lines.push(`*[View full investigation](${investigation.investigationLink})*`);
  }

  return lines.join('\n');
}

// ─── Related Resources Builder ──────────────────────────────────────────────

/**
 * Builds OpsItem RelatedResources from affected entities.
 * Attempts to construct valid ARNs from resource IDs.
 * Falls back to a generic format if the resource type can't be determined.
 */
function buildRelatedResources(
  healthEvent: HealthEventContext
): Array<{ ResourceType: string; ResourceUri: string }> {
  const resources: Array<{ ResourceType: string; ResourceUri: string }> = [];

  for (const resource of healthEvent.affectedResources) {
    const arn = buildResourceArn(resource.resourceId, healthEvent.service, healthEvent.region, healthEvent.sourceAccountId);
    if (arn) {
      resources.push({
        ResourceType: 'AWS::ARN',
        ResourceUri: arn,
      });
    }
  }

  return resources;
}

/**
 * Attempts to construct an ARN from a resource ID.
 * Handles common patterns: EC2 instances, RDS instances, Lambda functions, etc.
 */
function buildResourceArn(
  resourceId: string,
  service: string,
  region: string,
  accountId?: string
): string | null {
  // If it's already an ARN, use it directly
  if (resourceId.startsWith('arn:')) {
    return resourceId;
  }

  const account = accountId || '*';
  const svcLower = service.toLowerCase();

  // EC2 instances (i-xxxx)
  if (resourceId.startsWith('i-')) {
    return `arn:aws:ec2:${region}:${account}:instance/${resourceId}`;
  }

  // RDS instances (db-xxxx or named)
  if (svcLower === 'rds' || resourceId.startsWith('db-')) {
    return `arn:aws:rds:${region}:${account}:db:${resourceId}`;
  }

  // EBS volumes (vol-xxxx)
  if (resourceId.startsWith('vol-')) {
    return `arn:aws:ec2:${region}:${account}:volume/${resourceId}`;
  }

  // Lambda functions
  if (svcLower === 'lambda') {
    return `arn:aws:lambda:${region}:${account}:function:${resourceId}`;
  }

  // ELB / ALB
  if (resourceId.startsWith('arn:aws:elasticloadbalancing')) {
    return resourceId;
  }

  // Generic fallback: use the resource ID as-is in a service-specific ARN
  // This may not be a valid ARN but provides traceability
  const serviceMap: Record<string, string> = {
    ec2: 'ec2',
    rds: 'rds',
    lambda: 'lambda',
    elasticache: 'elasticache',
    redshift: 'redshift',
    s3: 's3',
    dynamodb: 'dynamodb',
    ecs: 'ecs',
    eks: 'eks',
  };

  const arnService = serviceMap[svcLower] || svcLower.toLowerCase();
  return `arn:aws:${arnService}:${region}:${account}:resource/${resourceId}`;
}

// ─── Handler ────────────────────────────────────────────────────────────────

export const handler = async (event: OpsItemCreatorInput): Promise<OpsItemCreatorOutput> => {
  console.log('Creating OpsItem for investigation result:', JSON.stringify(event, null, 2));

  const { investigationResult, healthEvent } = event;

  // Build OpsItem fields
  const severity = mapPriorityToSeverity(investigationResult.priority);
  const category = mapEventCategoryToOpsCategory(healthEvent.category, healthEvent.eventType);
  const title = `[${investigationResult.priority}] AWS Health: ${healthEvent.service} ${healthEvent.eventType} in ${healthEvent.region}`;
  const fullDescription = buildOpsItemDescription(investigationResult, healthEvent);
  // SSM OpsItem description is capped at 2048 chars by the API. The agent's
  // analysis can be considerably longer, so truncate gracefully and point
  // the operator at the OpsItem's OperationalData (full investigation
  // payload) and the agent's own investigation link for the complete text.
  const description = truncateForOpsItem(fullDescription);
  const relatedResources = buildRelatedResources(healthEvent);

  // Build OperationalData — structured metadata for correlation and lookup
  const operationalData: Record<string, { Value: string; Type: string }> = {
    '/healthEventArn': {
      Value: healthEvent.eventId,
      Type: 'SearchableString',
    },
    '/sourceAccountId': {
      Value: healthEvent.sourceAccountId || 'local',
      Type: 'SearchableString',
    },
    '/service': {
      Value: healthEvent.service,
      Type: 'SearchableString',
    },
    '/region': {
      Value: healthEvent.region,
      Type: 'SearchableString',
    },
    '/priority': {
      Value: investigationResult.priority,
      Type: 'SearchableString',
    },
    '/category': {
      Value: category,
      Type: 'SearchableString',
    },
    '/eventCategory': {
      Value: healthEvent.category,
      Type: 'SearchableString',
    },
    '/createdBy': {
      Value: 'HealthEventAnalyzer',
      Type: 'SearchableString',
    },
  };

  // Add investigation link if available
  if (investigationResult.investigationLink) {
    operationalData['/investigationLink'] = {
      Value: investigationResult.investigationLink,
      Type: 'SearchableString',
    };
  }

  // Add findings as JSON for reference
  if (investigationResult.findings.length > 0) {
    operationalData['/findings'] = {
      Value: JSON.stringify(investigationResult.findings),
      Type: 'SearchableString',
    };
  }

  // Add recommendations as JSON for reference
  if (investigationResult.recommendations.length > 0) {
    operationalData['/recommendations'] = {
      Value: JSON.stringify(investigationResult.recommendations),
      Type: 'SearchableString',
    };
  }

  // Add affected resource IDs for quick reference
  const affectedResourceIds = healthEvent.affectedResources.map(r => r.resourceId);
  if (affectedResourceIds.length > 0) {
    operationalData['/affectedResources'] = {
      Value: JSON.stringify(affectedResourceIds),
      Type: 'SearchableString',
    };
  }

  // Add resources in the OpsCenter native format (/aws/resources)
  // This makes them appear as "Related Resources" in the OpsCenter console.
  // OpsCenter rejects resources from a different region, so we filter to same-region
  // and put cross-region resources in a separate OperationalData field.
  const sameRegionResources: Array<{ arn: string | null }> = [];
  const crossRegionResources: string[] = [];

  for (const r of healthEvent.affectedResources) {
    const arn = r.resourceId.startsWith('arn:')
      ? r.resourceId
      : buildResourceArn(r.resourceId, healthEvent.service, healthEvent.region, healthEvent.sourceAccountId);
    if (!arn) continue;

    // IAM, Route53, CloudFront, etc. use "global" or no region — skip from /aws/resources
    const arnParts = arn.split(':');
    const arnRegion = arnParts.length >= 4 ? arnParts[3] : '';
    if (arnRegion === '' || arnRegion === 'global' || arnRegion !== AWS_REGION) {
      crossRegionResources.push(arn);
    } else {
      sameRegionResources.push({ arn });
    }
  }

  if (sameRegionResources.length > 0) {
    operationalData['/aws/resources'] = {
      Value: JSON.stringify(sameRegionResources),
      Type: 'SearchableString',
    };
  }

  if (crossRegionResources.length > 0) {
    operationalData['/crossRegionResources'] = {
      Value: JSON.stringify(crossRegionResources),
      Type: 'SearchableString',
    };
  }

  // Create the OpsItem
  const createParams: any = {
    Title: title,
    Description: description,
    Source: 'HealthEventAnalyzer',
    Severity: severity,
    Category: category,
    OperationalData: operationalData,
    Tags: [
      { Key: 'Source', Value: 'HealthEventAnalyzer' },
      { Key: 'HealthService', Value: healthEvent.service },
      { Key: 'HealthRegion', Value: healthEvent.region },
      { Key: 'Priority', Value: investigationResult.priority },
    ],
  };

  // Add related resources if we have valid ones (max 10 per API call)
  if (relatedResources.length > 0) {
    createParams.RelatedOpsItems = [];
    // Note: RelatedResources is set via OpsItemRelatedResources after creation
    // For now we include resource info in OperationalData
  }

  try {
    const response = await ssmClient.send(new CreateOpsItemCommand(createParams));
    const opsItemId = response.OpsItemId!;
    const opsItemUrl = `https://${AWS_REGION}.console.aws.amazon.com/systems-manager/opsitems/${opsItemId}?region=${AWS_REGION}`;

    console.log(`OpsItem created successfully: ${opsItemId}`);
    console.log(`OpsItem URL: ${opsItemUrl}`);
    console.log(`Severity: ${severity}, Category: ${category}`);

    return {
      opsItemId,
      opsItemUrl,
      investigationResult,
    };
  } catch (error: any) {
    console.error('Failed to create OpsItem:', error);

    // Don't fail the workflow if OpsItem creation fails — notifications should still go out
    // Return a degraded result with empty OpsItem info
    console.warn('Continuing workflow without OpsItem — notifications will still be sent');

    return {
      opsItemId: '',
      opsItemUrl: '',
      investigationResult,
    };
  }
};
