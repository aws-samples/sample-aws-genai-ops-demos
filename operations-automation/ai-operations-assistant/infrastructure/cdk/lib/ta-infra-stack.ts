import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { BaseInfraStack } from './base-infra-stack';

/**
 * G.O.A.T. TAInfraStack — ECR, S3, CodeBuild, and IAM for the Trusted Advisor Agent.
 * IAM role scoped to Trusted Advisor API.
 */
export class TAInfraStack extends BaseInfraStack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, {
      domainName: 'ta',
      exportPrefix: 'GOATTAAgent',
      imageTag: 'goat_ta_agent',
      domainPolicies: [
        // Trusted Advisor API
        new iam.PolicyStatement({
          sid: 'TrustedAdvisorAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            'trustedadvisor:DescribeChecks',
            'trustedadvisor:DescribeCheckResult',
            'trustedadvisor:ListRecommendations',
            'trustedadvisor:GetRecommendation',
            'trustedadvisor:ListChecks',
          ],
          resources: ['*'],
        }),
        // Legacy Support API for TA (some TA operations go through Support)
        new iam.PolicyStatement({
          sid: 'SupportTrustedAdvisorAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            'support:DescribeTrustedAdvisorChecks',
            'support:DescribeTrustedAdvisorCheckResult',
            'support:DescribeTrustedAdvisorCheckSummaries',
            'support:RefreshTrustedAdvisorCheck',
          ],
          resources: ['*'],
        }),
      ],
    }, props);
  }
}
