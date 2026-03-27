import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';

export class DataStack extends cdk.Stack {
  public readonly table: dynamodb.Table;
  public readonly bucket: s3.Bucket;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // DynamoDB table for Lambda function migration inventory
    this.table = new dynamodb.Table(this, 'MigrationTable', {
      tableName: 'lambda-runtime-migration',
      partitionKey: {
        name: 'function_arn',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // GSI for querying by migration status, sorted by priority score
    this.table.addGlobalSecondaryIndex({
      indexName: 'migration-status-index',
      partitionKey: {
        name: 'migration_status',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'priority_score',
        type: dynamodb.AttributeType.NUMBER,
      },
    });

    // S3 bucket for migration reports and packages
    this.bucket = new s3.Bucket(this, 'MigrationBucket', {
      bucketName: `lambda-migration-${cdk.Aws.ACCOUNT_ID}-${cdk.Aws.REGION}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      autoDeleteObjects: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Stack outputs
    new cdk.CfnOutput(this, 'TableName', {
      value: this.table.tableName,
      description: 'DynamoDB table for Lambda runtime migration inventory',
    });

    new cdk.CfnOutput(this, 'TableArn', {
      value: this.table.tableArn,
      description: 'DynamoDB table ARN for Lambda runtime migration inventory',
    });

    new cdk.CfnOutput(this, 'BucketName', {
      value: this.bucket.bucketName,
      description: 'S3 bucket for migration reports and packages',
    });

    new cdk.CfnOutput(this, 'BucketArn', {
      value: this.bucket.bucketArn,
      description: 'S3 bucket ARN for migration reports and packages',
    });
  }
}
