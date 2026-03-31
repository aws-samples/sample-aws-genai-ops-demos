import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

export interface DatabaseStackProps extends cdk.StackProps {
  environment: string;
  projectName: string;
  vpc: ec2.IVpc;
  privateDataSubnets: ec2.ISubnet[];
  databaseSecurityGroup: ec2.ISecurityGroup;
}

export class DatabaseStack extends cdk.Stack {
  public readonly rdsEndpoint: string;
  public readonly rdsInstanceId: string;

  constructor(scope: Construct, id: string, props: DatabaseStackProps) {
    super(scope, id, props);

    const { environment, projectName, vpc, privateDataSubnets, databaseSecurityGroup } = props;

    // Create the RDS credentials secret within CDK (sole owner).
    // The deploy script does NOT pre-create this secret — CDK manages it
    // end-to-end to avoid CloudFormation AlreadyExists conflicts.
    const dbSecret = new secretsmanager.Secret(this, 'DbSecret', {
      secretName: `${projectName}-${environment}-rds-credentials`,
      description: `RDS credentials for ${projectName}-${environment}`,
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: 'paymentadmin' }),
        generateStringKey: 'password',
        excludePunctuation: true,
        passwordLength: 20,
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // DB subnet group from private data subnets
    const subnetGroup = new rds.SubnetGroup(this, 'DatabaseSubnetGroup', {
      vpc,
      vpcSubnets: { subnets: privateDataSubnets },
      description: `Subnet group for ${projectName}-${environment} RDS`,
    });

    // Parameter group — postgres15 with SSL enforcement
    const parameterGroup = new rds.ParameterGroup(this, 'DatabaseParameterGroup', {
      engine: rds.DatabaseInstanceEngine.postgres({
        version: rds.PostgresEngineVersion.VER_15,
      }),
      description: `PostgreSQL parameters for ${projectName}-${environment}`,
      parameters: {
        'rds.force_ssl': '1',
      },
    });

    // RDS PostgreSQL 15 instance — mirrors original CloudFormation rds.yaml
    const dbInstance = new rds.DatabaseInstance(this, 'DatabaseInstance', {
      instanceIdentifier: `${projectName}-${environment}-postgres`,
      engine: rds.DatabaseInstanceEngine.postgres({
        version: rds.PostgresEngineVersion.VER_15,
      }),
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MICRO),
      vpc,
      vpcSubnets: { subnets: privateDataSubnets },
      credentials: rds.Credentials.fromSecret(dbSecret),
      databaseName: 'paymentdb',
      allocatedStorage: 20,
      storageType: rds.StorageType.GP3,
      storageEncrypted: true,
      subnetGroup,
      securityGroups: [databaseSecurityGroup],
      publiclyAccessible: false,
      multiAz: false,
      parameterGroup,
      backupRetention: cdk.Duration.days(1),
      preferredBackupWindow: '03:00-04:00',
      copyTagsToSnapshot: true,
      deleteAutomatedBackups: true,
      cloudwatchLogsExports: ['postgresql'],
      autoMinorVersionUpgrade: true,
      deletionProtection: false,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.rdsEndpoint = dbInstance.dbInstanceEndpointAddress;
    this.rdsInstanceId = dbInstance.instanceIdentifier;

    // CloudFormation Outputs
    new cdk.CfnOutput(this, 'RdsEndpoint', {
      description: 'RDS PostgreSQL endpoint address',
      value: dbInstance.dbInstanceEndpointAddress,
    });

    new cdk.CfnOutput(this, 'RdsPort', {
      description: 'RDS PostgreSQL port',
      value: dbInstance.dbInstanceEndpointPort,
    });

    new cdk.CfnOutput(this, 'RdsInstanceId', {
      description: 'RDS instance identifier',
      value: dbInstance.instanceIdentifier,
    });

    new cdk.CfnOutput(this, 'DatabaseName', {
      description: 'Name of the database',
      value: 'paymentdb',
    });
  }
}
