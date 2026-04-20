import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { BaseInfraStack } from './base-infra-stack';

/**
 * G.O.A.T. CostInfraStack — ECR, S3, CodeBuild, and IAM for the Cost Agent.
 * IAM role scoped to Cost Explorer and Cost Optimization Hub APIs.
 */
export class CostInfraStack extends BaseInfraStack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, {
      domainName: 'cost',
      exportPrefix: 'GOATCostAgent',
      imageTag: 'goat_cost_agent',
      domainPolicies: [
        // Cost Explorer API
        new iam.PolicyStatement({
          sid: 'CostExplorerAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            'ce:GetCostAndUsage',
            'ce:GetCostForecast',
            'ce:GetDimensionValues',
            'ce:GetTags',
          ],
          resources: ['*'],
        }),
        // Cost Optimization Hub API
        new iam.PolicyStatement({
          sid: 'CostOptimizationHubAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            'cost-optimization-hub:ListRecommendations',
            'cost-optimization-hub:GetRecommendation',
            'cost-optimization-hub:ListEnrollmentStatuses',
          ],
          resources: ['*'],
        }),
      ],
    }, props);
  }
}
