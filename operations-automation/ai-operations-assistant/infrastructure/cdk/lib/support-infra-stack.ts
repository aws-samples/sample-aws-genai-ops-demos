import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { BaseInfraStack } from './base-infra-stack';

/**
 * G.O.A.T. SupportInfraStack — ECR, S3, CodeBuild, and IAM for the Support Agent.
 * IAM role scoped to AWS Support API.
 */
export class SupportInfraStack extends BaseInfraStack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, {
      domainName: 'support',
      exportPrefix: 'GOATSupportAgent',
      imageTag: 'goat_support_agent',
      domainPolicies: [
        // AWS Support API
        new iam.PolicyStatement({
          sid: 'SupportAPIAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            'support:DescribeCases',
            'support:DescribeCommunications',
            'support:DescribeServices',
            'support:DescribeSeverityLevels',
          ],
          resources: ['*'],
        }),
      ],
    }, props);
  }
}
