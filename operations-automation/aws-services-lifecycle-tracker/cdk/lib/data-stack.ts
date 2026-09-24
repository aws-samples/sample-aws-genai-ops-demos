import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import * as path from 'path';
import * as fs from 'fs';

export class DataStack extends cdk.Stack {
  public readonly lifecycleTable: dynamodb.Table;
  public readonly configTable: dynamodb.Table;
  public readonly stateTable: dynamodb.Table;
  public readonly inventoryTable: dynamodb.Table;
  public readonly actionPlanTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Main lifecycle data table
    this.lifecycleTable = new dynamodb.Table(this, 'LifecycleTable', {
      tableName: 'aws-services-lifecycle',
      partitionKey: {
        name: 'service_name',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'item_id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: true,
      },
      removalPolicy: cdk.RemovalPolicy.RETAIN, // Protect production data
    });

    // GSI for querying by status
    this.lifecycleTable.addGlobalSecondaryIndex({
      indexName: 'status-index',
      partitionKey: {
        name: 'status',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'deprecation_date',
        type: dynamodb.AttributeType.STRING,
      },
    });

    // GSI for tracking extraction history
    this.lifecycleTable.addGlobalSecondaryIndex({
      indexName: 'extraction-date-index',
      partitionKey: {
        name: 'service_name',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'extraction_date',
        type: dynamodb.AttributeType.STRING,
      },
    });

    // Service configuration table
    this.configTable = new dynamodb.Table(this, 'ConfigTable', {
      tableName: 'service-extraction-config',
      partitionKey: {
        name: 'service_name',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: true,
      },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Backend-owned runtime state table (issue #116, Option B).
    // Extraction metadata (extraction_count, last_extraction, success_rate,
    // last_refresh_origin, last_extraction_duration) and control rows such as
    // the last AWS Health cross-check (_health_match) live
    // here, physically separated from the repo-owned configuration table so no
    // deploy-time writer can touch runtime state: the populator has no grant on
    // this table, and the backend has no full-item write on the config table.
    // TTL enabled for the concurrency-lock row's expires_at-based cleanup.
    this.stateTable = new dynamodb.Table(this, 'StateTable', {
      tableName: 'service-extraction-state',
      partitionKey: {
        name: 'service_name',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: true,
      },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Account inventory table (issue #116 follow-on): discovered assets from
    // account scans live here, fully decoupled from the public deprecation
    // facts in aws-services-lifecycle. Discovery is this table's only writer;
    // reconciliation scans stay confined to this small table instead of
    // sweeping the growing facts table. Same key shape as the lifecycle table
    // so read paths can union rows, and ready to grow account_id/region
    // dimensions for the multi-account roadmap (#99 I4).
    this.inventoryTable = new dynamodb.Table(this, 'InventoryTable', {
      tableName: 'aws-account-inventory',
      partitionKey: {
        name: 'service_name',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'item_id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: true,
      },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Action Plan table for tracking deprecation remediation
    this.actionPlanTable = new dynamodb.Table(this, 'ActionPlanTable', {
      tableName: 'deprecation-action-plans',
      partitionKey: {
        name: 'plan_id',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: true,
      },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // GSI for querying by owner
    this.actionPlanTable.addGlobalSecondaryIndex({
      indexName: 'owner-index',
      partitionKey: {
        name: 'owner',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'created_at',
        type: dynamodb.AttributeType.STRING,
      },
    });

    // GSI for querying by status
    this.actionPlanTable.addGlobalSecondaryIndex({
      indexName: 'plan-status-index',
      partitionKey: {
        name: 'plan_status',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'target_date',
        type: dynamodb.AttributeType.STRING,
      },
    });

    // Outputs for other stacks
    new cdk.CfnOutput(this, 'LifecycleTableName', {
      value: this.lifecycleTable.tableName,
      description: 'DynamoDB table for AWS services lifecycle data',
      exportName: 'AWSServicesLifecycleTrackerLifecycleTableName',
    });

    new cdk.CfnOutput(this, 'LifecycleTableArn', {
      value: this.lifecycleTable.tableArn,
      description: 'DynamoDB table ARN for lifecycle data',
      exportName: 'AWSServicesLifecycleTrackerLifecycleTableArn',
    });

    new cdk.CfnOutput(this, 'ConfigTableName', {
      value: this.configTable.tableName,
      description: 'DynamoDB table for service extraction configuration',
      exportName: 'AWSServicesLifecycleTrackerConfigTableName',
    });

    new cdk.CfnOutput(this, 'ConfigTableArn', {
      value: this.configTable.tableArn,
      description: 'DynamoDB table ARN for service configuration',
      exportName: 'AWSServicesLifecycleTrackerConfigTableArn',
    });

    new cdk.CfnOutput(this, 'InventoryTableName', {
      value: this.inventoryTable.tableName,
      description: 'DynamoDB table for discovered account inventory (issue #116)',
      exportName: 'AWSServicesLifecycleTrackerInventoryTableName',
    });

    new cdk.CfnOutput(this, 'InventoryTableArn', {
      value: this.inventoryTable.tableArn,
      description: 'DynamoDB table ARN for discovered account inventory',
      exportName: 'AWSServicesLifecycleTrackerInventoryTableArn',
    });

    new cdk.CfnOutput(this, 'StateTableName', {
      value: this.stateTable.tableName,
      description: 'DynamoDB table for backend-owned runtime state (issue #116)',
      exportName: 'AWSServicesLifecycleTrackerStateTableName',
    });

    new cdk.CfnOutput(this, 'StateTableArn', {
      value: this.stateTable.tableArn,
      description: 'DynamoDB table ARN for backend-owned runtime state',
      exportName: 'AWSServicesLifecycleTrackerStateTableArn',
    });

    new cdk.CfnOutput(this, 'ActionPlanTableName', {
      value: this.actionPlanTable.tableName,
      description: 'DynamoDB table for deprecation action plans',
      exportName: 'AWSServicesLifecycleTrackerActionPlanTableName',
    });

    new cdk.CfnOutput(this, 'ActionPlanTableArn', {
      value: this.actionPlanTable.tableArn,
      description: 'DynamoDB table ARN for action plans',
      exportName: 'AWSServicesLifecycleTrackerActionPlanTableArn',
    });

    // Custom Resource to populate service configurations
    this.createServiceConfigPopulator();
  }

  private createServiceConfigPopulator() {
    // Read service_configs.json
    const configPath = path.join(__dirname, '../../scripts/service_configs.json');
    const serviceConfigs = JSON.parse(fs.readFileSync(configPath, 'utf-8'));

    // Lambda function to populate configurations. The code is shared with the
    // Terraform path (terraform/data.tf): one populator, two IaC front doors.
    const populatorPath = path.join(__dirname, '../../scripts/service_config_populator.py');
    const populatorFunction = new lambda.Function(this, 'ServiceConfigPopulator', {
      runtime: lambda.Runtime.PYTHON_3_14,
      handler: 'index.handler',
      timeout: cdk.Duration.minutes(5),
      code: lambda.Code.fromInline(fs.readFileSync(populatorPath, 'utf-8')),
      environment: {
        CONFIG_TABLE_NAME: this.configTable.tableName,
      },
    });

    // Write (upsert/delete) plus read (scan for the reconcile step)
    this.configTable.grantReadWriteData(populatorFunction);

    // Create custom resource provider
    const provider = new cr.Provider(this, 'ServiceConfigProvider', {
      onEventHandler: populatorFunction,
    });

    // Create custom resource with explicit dependency on the table
    const configResource = new cdk.CustomResource(this, 'ServiceConfigResource', {
      serviceToken: provider.serviceToken,
      properties: {
        ServiceConfigs: JSON.stringify(serviceConfigs.services),
        TableName: this.configTable.tableName,
        // Force the populator to run on every deployment. This is SAFE (and
        // desirable) because the populator only writes static, repo-owned
        // config fields and never touches backend-owned runtime state (#116).
        Timestamp: Date.now().toString(),
      },
    });

    // Ensure the custom resource waits for the table to be fully created
    configResource.node.addDependency(this.configTable);
  }
}