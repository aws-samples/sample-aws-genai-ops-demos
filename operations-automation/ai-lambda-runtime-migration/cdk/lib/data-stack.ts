import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';

export class DataStack extends cdk.Stack {
  public readonly table: dynamodb.Table;
  public readonly bucket: s3.Bucket;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // DynamoDB table for Lambda function migration inventory.
    //
    // Partition key is `item_key` = fn#<account_id>#<region>#<function_arn>
    // (v1.1 schema change, design §4): the bare function ARN collides across
    // accounts in a hub-and-spoke scan, so the key carries account + region.
    // This is a BREAKING schema change from the v1 `function_arn` key — because
    // the partition key changed, redeploying over an existing table RECREATES
    // it (RemovalPolicy.DESTROY), resetting the inventory. Documented in README.
    this.table = new dynamodb.Table(this, 'MigrationTable', {
      tableName: 'lambda-runtime-migration',
      partitionKey: {
        name: 'item_key',
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
      description: 'DynamoDB table for Lambda runtime migration inventory (item_key = fn#<account>#<region>#<arn>; schema change recreates the table)',
    });

    new cdk.CfnOutput(this, 'BucketName', {
      value: this.bucket.bucketName,
      description: 'S3 bucket for migration reports and packages',
    });
  }
}
