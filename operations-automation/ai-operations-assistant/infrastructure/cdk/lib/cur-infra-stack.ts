import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { BaseInfraStack } from './base-infra-stack';

/**
 * G.O.A.T. CURInfraStack — ECR, S3, CodeBuild, and IAM for the CUR Agent.
 * IAM role scoped to Athena (query execution) and S3 (CUR data access).
 */
export class CURInfraStack extends BaseInfraStack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, {
      domainName: 'cur',
      exportPrefix: 'GOATCURAgent',
      imageTag: 'goat_cur_agent',
      domainPolicies: [
        // Athena query execution
        new iam.PolicyStatement({
          sid: 'AthenaAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            'athena:StartQueryExecution',
            'athena:GetQueryExecution',
            'athena:GetQueryResults',
            'athena:StopQueryExecution',
            'athena:GetWorkGroup',
          ],
          resources: [
            `arn:aws:athena:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:workgroup/*`,
          ],
        }),
        // S3 access for CUR data and Athena query results
        new iam.PolicyStatement({
          sid: 'S3CURDataAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            's3:GetObject',
            's3:ListBucket',
            's3:GetBucketLocation',
          ],
          resources: [
            `arn:aws:s3:::*cur*`,
            `arn:aws:s3:::*cur*/*`,
            `arn:aws:s3:::*athena-query-results*`,
            `arn:aws:s3:::*athena-query-results*/*`,
          ],
        }),
        // S3 write for Athena query results
        new iam.PolicyStatement({
          sid: 'S3AthenaResultsWrite',
          effect: iam.Effect.ALLOW,
          actions: [
            's3:PutObject',
            's3:GetObject',
            's3:AbortMultipartUpload',
            's3:GetBucketLocation',
            's3:ListBucket',
          ],
          resources: [
            `arn:aws:s3:::*athena-query-results*`,
            `arn:aws:s3:::*athena-query-results*/*`,
          ],
        }),
        // Glue catalog access for Athena table metadata
        new iam.PolicyStatement({
          sid: 'GlueCatalogAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            'glue:GetDatabase',
            'glue:GetDatabases',
            'glue:GetTable',
            'glue:GetTables',
            'glue:GetPartitions',
          ],
          resources: [
            `arn:aws:glue:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:catalog`,
            `arn:aws:glue:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:database/*`,
            `arn:aws:glue:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:table/*/*`,
          ],
        }),
      ],
    }, props);
  }
}
