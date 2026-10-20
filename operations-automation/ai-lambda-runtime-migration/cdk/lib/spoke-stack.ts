import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { DISCOVER_READ_ACTIONS, hubPipelineRoleName, SPOKE_ROLE_NAME } from './scan-permissions';

export interface SpokeStackProps extends cdk.StackProps {
  /** Hub account that runs the pipeline and will assume the spoke role. */
  hubAccountId: string;
  /** Optional sts:ExternalId the hub must present (confused-deputy guard). */
  externalId?: string;
}

/**
 * Spoke side of the opt-in multi-account scan (design §5).
 *
 * Deployed in a MEMBER account, this stack contains exactly one resource: the
 * read-only IAM role the hub's pipeline assumes to run Discover + Assess there.
 * No data, no compute, no Lambda lives in the spoke. The role trusts only the
 * hub account, pinned to the hub pipeline role, optionally with an ExternalId.
 * Its permissions are EXACTLY DISCOVER_READ_ACTIONS, the single action list
 * shared with the hub role through scan-permissions.ts — never re-listed here.
 *
 * Deploy it by hand into one account for a test, or let the StackSet in
 * OrgStack roll it out to every account of an organization / OU.
 *
 * No solution-adoption tracking tag/description: the demo's single tracked
 * stack is the main (Pipeline) stack.
 */
export class SpokeStack extends cdk.Stack {
  public readonly scanRole: iam.Role;

  constructor(scope: Construct, id: string, props: SpokeStackProps) {
    super(scope, id, props);

    if (!/^\d{12}$/.test(props.hubAccountId)) {
      throw new Error(`hubAccountId must be a 12-digit AWS account id, got '${props.hubAccountId}'`);
    }

    // The deployed hub pipeline role is region-suffixed (PipelineStack names it
    // with hubPipelineRoleName). The spoke role lives in the SAME region as the
    // hub (the StackSet targets only this.region), so this.region — the member
    // account's AWS::Region pseudo-parameter at deploy time — resolves to the
    // hub's region, matching the actual deployed hub role name. Never a literal.
    const hubRoleName = hubPipelineRoleName(this.region);

    const conditions: Record<string, Record<string, string>> = {
      // Trusting the account root keeps the trust policy valid even before the
      // hub role exists; the condition pins it to the pipeline role anyway.
      ArnEquals: { 'aws:PrincipalArn': `arn:${this.partition}:iam::${props.hubAccountId}:role/${hubRoleName}` },
    };
    if (props.externalId) {
      conditions.StringEquals = { 'sts:ExternalId': props.externalId };
    }

    this.scanRole = new iam.Role(this, 'ScanRole', {
      roleName: SPOKE_ROLE_NAME, // fixed: the hub derives the ARN from the account id
      description: 'Read-only role assumed by the AI Lambda Runtime Migration hub to scan this account',
      assumedBy: new iam.PrincipalWithConditions(new iam.AccountPrincipal(props.hubAccountId), conditions),
      maxSessionDuration: cdk.Duration.hours(1),
    });

    this.scanRole.addToPolicy(new iam.PolicyStatement({
      sid: 'LambdaRuntimeMigrationDiscoverReads',
      actions: [...DISCOVER_READ_ACTIONS],
      resources: ['*'], // read-only List/Describe/Get calls: no resource scoping available
    }));

    new cdk.CfnOutput(this, 'ScanRoleArn', {
      value: this.scanRole.roleArn,
      description: 'Role the hub assumes to scan this account',
    });
    new cdk.CfnOutput(this, 'HubAccountId', { value: props.hubAccountId });
  }
}
