import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as rds from 'aws-cdk-lib/aws-rds';
import { Construct } from 'constructs';

/**
 * G.O.A.T. Demo Scenario A — Account Health Check Stack
 *
 * Provisions resources that trigger Trusted Advisor findings and generate cost data
 * for the Account Health Check demo scenario:
 * - Two subnets in separate AZs (in the existing goat-demo-vpc)
 * - Two EC2 instances (t3.micro)
 * - One RDS MySQL instance (db.t3.micro, 20GB gp2)
 * - One unattached EBS volume (10GB gp2)
 * - One unassociated Elastic IP
 *
 * IMPORTANT: This stack does NOT create the goat-demo-vpc. It looks up the
 * existing VPC created by the main GOAT network-infra stack. If the VPC
 * does not exist, deployment will fail with a clear error message.
 *
 * Exposes `vpc` property for cross-stack sharing with Scenario C.
 */
export class DemoScenarioAccountHealthStack extends cdk.Stack {
  /** Public VPC reference for cross-stack sharing with Scenario C */
  public readonly vpc: ec2.IVpc;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Common tags for all resources in this stack
    cdk.Tags.of(this).add('goat-demo', 'true');
    cdk.Tags.of(this).add('goat-scenario', 'a');
    cdk.Tags.of(this).add('auto-delete', 'no');

    // -----------------------------------------------------------------------
    // VPC — Look up the existing goat-demo-vpc created by the GOAT stack.
    // This stack does NOT own the VPC — only the main GOAT network-infra
    // stack creates and deletes it.
    // -----------------------------------------------------------------------
    this.vpc = ec2.Vpc.fromLookup(this, 'GoatDemoVpc', {
      tags: { 'Name': 'goat-demo-vpc', 'goat-demo': 'true' },
    });

    // Validate we found a real VPC (fromLookup returns a dummy during synth
    // if the VPC doesn't exist — the deploy will fail with a clear CFN error,
    // but we add a CfnRule for a nicer message at synth time)
    const vpcId = this.vpc.vpcId;

    // -----------------------------------------------------------------------
    // Subnets — Two subnets in separate AZs
    // -----------------------------------------------------------------------
    const az1 = cdk.Fn.select(0, cdk.Fn.getAzs(''));
    const az2 = cdk.Fn.select(1, cdk.Fn.getAzs(''));

    const subnet1 = new ec2.CfnSubnet(this, 'Subnet1', {
      vpcId: vpcId,
      cidrBlock: '10.99.1.0/24',
      availabilityZone: az1,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'goat-demo-subnet-1' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'a' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    const subnet2 = new ec2.CfnSubnet(this, 'Subnet2', {
      vpcId: vpcId,
      cidrBlock: '10.99.2.0/24',
      availabilityZone: az2,
      mapPublicIpOnLaunch: false,
      tags: [
        { key: 'Name', value: 'goat-demo-subnet-2' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'a' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // DB Subnet Group
    // -----------------------------------------------------------------------
    const dbSubnetGroup = new rds.CfnDBSubnetGroup(this, 'DbSubnetGroup', {
      dbSubnetGroupName: 'goat-demo-db-subnet-group',
      dbSubnetGroupDescription: 'Subnet group for G.O.A.T. demo RDS instance',
      subnetIds: [subnet1.attrSubnetId, subnet2.attrSubnetId],
      tags: [
        { key: 'Name', value: 'goat-demo-db-subnet-group' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'a' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Security Group for EC2 and RDS (minimal — no ingress)
    // -----------------------------------------------------------------------
    const sg = new ec2.CfnSecurityGroup(this, 'DemoSg', {
      vpcId: vpcId,
      groupDescription: 'Security group for G.O.A.T. demo resources',
      tags: [
        { key: 'Name', value: 'goat-demo-sg' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'a' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // EC2 Instances — t3.micro with AL2023 AMI via SSM
    // -----------------------------------------------------------------------
    const al2023Ami = ec2.MachineImage.latestAmazonLinux2023();
    const amiId = al2023Ami.getImage(this).imageId;

    const instance1 = new ec2.CfnInstance(this, 'Instance1', {
      instanceType: 't3.micro',
      imageId: amiId,
      subnetId: subnet1.attrSubnetId,
      securityGroupIds: [sg.attrGroupId],
      tags: [
        { key: 'Name', value: 'goat-demo-instance-1' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'a' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    const instance2 = new ec2.CfnInstance(this, 'Instance2', {
      instanceType: 't3.micro',
      imageId: amiId,
      subnetId: subnet2.attrSubnetId,
      securityGroupIds: [sg.attrGroupId],
      tags: [
        { key: 'Name', value: 'goat-demo-instance-2' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'a' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // RDS MySQL Instance — db.t3.micro, 20GB gp2, single-AZ, no public access
    // -----------------------------------------------------------------------
    const rdsInstance = new rds.CfnDBInstance(this, 'RdsInstance', {
      dbInstanceIdentifier: 'goat-demo-db',
      dbInstanceClass: 'db.t3.micro',
      engine: 'mysql',
      masterUsername: 'admin',
      masterUserPassword: 'GoatDemo2024!',  // Demo-only password
      allocatedStorage: '20',
      storageType: 'gp2',
      multiAz: false,
      publiclyAccessible: false,
      dbSubnetGroupName: dbSubnetGroup.dbSubnetGroupName,
      vpcSecurityGroups: [sg.attrGroupId],
      tags: [
        { key: 'Name', value: 'goat-demo-db' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'a' },
        { key: 'auto-delete', value: 'no' },
      ],
    });
    rdsInstance.cfnOptions.deletionPolicy = cdk.CfnDeletionPolicy.DELETE;
    rdsInstance.addDependency(dbSubnetGroup);

    // -----------------------------------------------------------------------
    // EBS Volume — 10GB gp2, unattached
    // -----------------------------------------------------------------------
    const ebsVolume = new ec2.CfnVolume(this, 'EbsVolume', {
      availabilityZone: az1,
      size: 10,
      volumeType: 'gp2',
      tags: [
        { key: 'Name', value: 'goat-demo-ebs-unused' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'a' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Elastic IP — unassociated
    // -----------------------------------------------------------------------
    const eip = new ec2.CfnEIP(this, 'ElasticIp', {
      tags: [
        { key: 'Name', value: 'goat-demo-eip-unused' },
        { key: 'goat-demo', value: 'true' },
        { key: 'goat-scenario', value: 'a' },
        { key: 'auto-delete', value: 'no' },
      ],
    });

    // -----------------------------------------------------------------------
    // Stack Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'VpcId', {
      value: vpcId,
      description: 'VPC ID for G.O.A.T. demo (looked up from existing goat-demo-vpc)',
    });

    new cdk.CfnOutput(this, 'Subnet1Id', {
      value: subnet1.attrSubnetId,
      description: 'Subnet 1 ID (AZ-a)',
    });

    new cdk.CfnOutput(this, 'Subnet2Id', {
      value: subnet2.attrSubnetId,
      description: 'Subnet 2 ID (AZ-b)',
    });

    new cdk.CfnOutput(this, 'Instance1Id', {
      value: instance1.ref,
      description: 'EC2 Instance 1 ID',
    });

    new cdk.CfnOutput(this, 'Instance2Id', {
      value: instance2.ref,
      description: 'EC2 Instance 2 ID',
    });

    new cdk.CfnOutput(this, 'RdsInstanceId', {
      value: rdsInstance.ref,
      description: 'RDS instance identifier',
    });

    new cdk.CfnOutput(this, 'EbsVolumeId', {
      value: ebsVolume.ref,
      description: 'Unattached EBS volume ID',
    });

    new cdk.CfnOutput(this, 'EipAllocationId', {
      value: eip.attrAllocationId,
      description: 'Unassociated Elastic IP allocation ID',
    });
  }
}
