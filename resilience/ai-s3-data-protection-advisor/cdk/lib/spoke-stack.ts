import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { SCAN_ACTIONS, SPOKE_ROLE_NAME, hubRoleName } from './scan-permissions';

export interface SpokeStackProps extends cdk.StackProps {
  /** Account the hub pipeline runs in. */
  hubAccountId: string;
  /** Region the hub is deployed in: it is part of the hub role name the trust policy pins. */
  hubRegion: string;
}

/**
 * The read-only role the hub pipeline assumes to scan a member account, and nothing else. Rolled out by the
 * Org stack's StackSet, or deployed by hand in one account. No tracking description: the Data stack carries it.
 */
export class SpokeStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: SpokeStackProps) {
    super(scope, id, props);

    if (!/^\d{12}$/.test(props.hubAccountId)) {
      throw new Error(`hubAccountId must be a 12-digit account id, got '${props.hubAccountId}'`);
    }
    if (!/^[a-z]{2}(-[a-z]+)+-\d$/.test(props.hubRegion)) {
      throw new Error(`hubRegion must be a region name, got '${props.hubRegion}'`);
    }

    // Trust the hub account root with a condition on the hub role ARN: the policy stays valid when the hub role
    // does not exist yet or is recreated, which trusting the role ARN as the principal would not.
    const hubRoleArn = `arn:${this.partition}:iam::${props.hubAccountId}:role/${hubRoleName(props.hubRegion)}`;
    const role = new iam.Role(this, 'ScanRole', {
      roleName: SPOKE_ROLE_NAME,
      description: 'Read-only role the S3 Data Protection Advisor hub assumes to scan this account',
      assumedBy: new iam.PrincipalWithConditions(new iam.AccountPrincipal(props.hubAccountId), {
        ArnEquals: { 'aws:PrincipalArn': hubRoleArn },
      }),
      maxSessionDuration: cdk.Duration.hours(1),
    });
    role.addToPolicy(new iam.PolicyStatement({
      sid: 'ScanReadOnly',
      actions: [...SCAN_ACTIONS],
      resources: ['*'], // read-only describe/list calls without resource-level permissions
    }));

    new cdk.CfnOutput(this, 'ScanRoleArn', { value: role.roleArn, description: 'Role the hub assumes' });
  }
}
