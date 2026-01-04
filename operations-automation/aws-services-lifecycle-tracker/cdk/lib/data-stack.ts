import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import * as path from 'path';
import * as fs from 'fs';

export class DataStack extends cdk.Stack {
  public readonly lifecycleTable: dynamodb.Table;
  public readonly configTable: dynamodb.Table;

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

    // Custom Resource to populate service configurations
    this.createServiceConfigPopulator();
  }

  private createServiceConfigPopulator() {
    // Read service_configs.json
    const configPath = path.join(__dirname, '../../scripts/service_configs.json');
    const serviceConfigs = JSON.parse(fs.readFileSync(configPath, 'utf-8'));

    // Lambda function to populate configurations
    const populatorFunction = new lambda.Function(this, 'ServiceConfigPopulator', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      timeout: cdk.Duration.minutes(5),
      code: lambda.Code.fromInline(`
import json
import boto3
from decimal import Decimal

def handler(event, context):
    """Populate DynamoDB with service configurations"""
    
    request_type = event['RequestType']
    
    # Only populate on Create and Update
    if request_type == 'Delete':
        return {
            'PhysicalResourceId': 'ServiceConfigPopulator',
            'Data': {'Message': 'Delete operation - no action needed'}
        }
    
    try:
        # Get service configurations from event
        services_config = json.loads(event['ResourceProperties']['ServiceConfigs'])
        
        dynamodb = boto3.resource('dynamodb')
        config_table = dynamodb.Table('service-extraction-config')
        
        print(f"Populating {len(services_config)} service configurations...")
        
        for service_name, config in services_config.items():
            # Add service_name (required for DynamoDB key)
            config['service_name'] = service_name
            
            # Put item in DynamoDB
            config_table.put_item(Item=config)
            print(f"✅ {config.get('name', service_name)}: Configuration saved")
        
        return {
            'PhysicalResourceId': 'ServiceConfigPopulator',
            'Data': {
                'Message': f'Successfully populated {len(services_config)} service configurations',
                'ServiceCount': len(services_config)
            }
        }
        
    except Exception as e:
        print(f"Error: {str(e)}")
        raise
`),
      environment: {
        CONFIG_TABLE_NAME: this.configTable.tableName,
      },
    });

    // Grant permissions to write to config table
    this.configTable.grantWriteData(populatorFunction);

    // Create custom resource provider
    const provider = new cr.Provider(this, 'ServiceConfigProvider', {
      onEventHandler: populatorFunction,
    });

    // Create custom resource
    new cdk.CustomResource(this, 'ServiceConfigResource', {
      serviceToken: provider.serviceToken,
      properties: {
        ServiceConfigs: JSON.stringify(serviceConfigs.services),
        // Add timestamp to force update on every deployment
        Timestamp: Date.now().toString(),
      },
    });
  }
}