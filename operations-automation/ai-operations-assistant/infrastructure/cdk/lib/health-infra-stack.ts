import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { BaseInfraStack } from './base-infra-stack';

/**
 * G.O.A.T. HealthInfraStack — ECR, S3, CodeBuild, and IAM for the Health Agent.
 * IAM role scoped to AWS Health API.
 */
export class HealthInfraStack extends BaseInfraStack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, {
      domainName: 'health',
      exportPrefix: 'GOATHealthAgent',
      imageTag: 'goat_health_agent',
      domainPolicies: [
        // AWS Health API
        new iam.PolicyStatement({
          sid: 'HealthAPIAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            'health:DescribeEvents',
            'health:DescribeEventDetails',
            'health:DescribeAffectedEntities',
            'health:DescribeEventTypes',
          ],
          resources: ['*'],
        }),
      ],
    }, props);
  }
}
