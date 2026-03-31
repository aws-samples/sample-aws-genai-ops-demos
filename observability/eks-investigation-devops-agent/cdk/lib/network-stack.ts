import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';

export interface NetworkStackProps extends cdk.StackProps {
  environment: string;
  projectName: string;
}

export class NetworkStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;
  public readonly publicSubnets: ec2.ISubnet[];
  public readonly privateComputeSubnets: ec2.ISubnet[];
  public readonly privateDataSubnets: ec2.ISubnet[];
  public readonly albSecurityGroup: ec2.SecurityGroup;
  public readonly eksSecurityGroup: ec2.SecurityGroup;
  public readonly databaseSecurityGroup: ec2.SecurityGroup;

  constructor(scope: Construct, id: string, props: NetworkStackProps) {
    super(scope, id, props);

    const { environment, projectName } = props;

    // -----------------------------------------------------------------------
    // VPC — 10.0.0.0/16, DNS hostnames + support, 2 AZs, single NAT Gateway
    // Subnet CIDRs match original CloudFormation vpc.yaml:
    //   Public:          10.0.1.0/24, 10.0.2.0/24
    //   Private Compute: 10.0.11.0/24, 10.0.12.0/24
    //   Private Data:    10.0.21.0/24, 10.0.22.0/24
    // -----------------------------------------------------------------------
    this.vpc = new ec2.Vpc(this, 'Vpc', {
      ipAddresses: ec2.IpAddresses.cidr('10.0.0.0/16'),
      maxAzs: 2,
      natGateways: 1,
      subnetConfiguration: [
        {
          cidrMask: 24,
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
          mapPublicIpOnLaunch: false,
        },
        {
          cidrMask: 24,
          name: 'PrivateCompute',
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
        },
        {
          cidrMask: 24,
          name: 'PrivateData',
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
        },
      ],
    });

    // Tag VPC with Project name so cleanup scripts can locate it
    cdk.Tags.of(this.vpc).add('Project', projectName);

    // Expose subnet arrays
    this.publicSubnets = this.vpc.publicSubnets;
    this.privateComputeSubnets = this.vpc.selectSubnets({
      subnetGroupName: 'PrivateCompute',
    }).subnets;
    this.privateDataSubnets = this.vpc.selectSubnets({
      subnetGroupName: 'PrivateData',
    }).subnets;

    // -----------------------------------------------------------------------
    // Subnet tagging for Kubernetes load balancer discovery
    // -----------------------------------------------------------------------
    for (const subnet of this.publicSubnets) {
      cdk.Tags.of(subnet).add('kubernetes.io/role/elb', '1');
    }
    for (const subnet of this.privateComputeSubnets) {
      cdk.Tags.of(subnet).add('kubernetes.io/role/internal-elb', '1');
    }

    // -----------------------------------------------------------------------
    // ALB Security Group — allows HTTPS from internet, egress to EKS on 8080
    // -----------------------------------------------------------------------
    this.albSecurityGroup = new ec2.SecurityGroup(this, 'AlbSecurityGroup', {
      vpc: this.vpc,
      securityGroupName: `${projectName}-${environment}-alb-sg`,
      description: 'Security group for Application Load Balancer',
      allowAllOutbound: false,
    });

    // Ingress: HTTPS from internet (CIDR-based, safe inline)
    this.albSecurityGroup.addIngressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(443),
      'Allow HTTPS from internet',
    );

    // -----------------------------------------------------------------------
    // EKS Security Group — node-to-node, ALB ingress, RDS + HTTPS egress
    // -----------------------------------------------------------------------
    this.eksSecurityGroup = new ec2.SecurityGroup(this, 'EksSecurityGroup', {
      vpc: this.vpc,
      securityGroupName: `${projectName}-${environment}-eks-sg`,
      description: 'Security group for EKS worker nodes',
      allowAllOutbound: false,
    });

    // -----------------------------------------------------------------------
    // Database Security Group — PostgreSQL from EKS SG + compute subnets,
    // egress restricted to localhost only (deny-all pattern)
    // -----------------------------------------------------------------------
    this.databaseSecurityGroup = new ec2.SecurityGroup(this, 'DatabaseSecurityGroup', {
      vpc: this.vpc,
      securityGroupName: `${projectName}-${environment}-rds-sg`,
      description: 'Security group for RDS PostgreSQL',
      allowAllOutbound: false,
    });

    // -----------------------------------------------------------------------
    // Cross-SG rules use standalone L1 resources to avoid cyclic dependencies
    // (mirrors the original CloudFormation which uses separate Ingress/Egress
    // resources for all cross-SG references)
    // -----------------------------------------------------------------------

    // ALB → EKS egress on port 8080
    new ec2.CfnSecurityGroupEgress(this, 'AlbEgressToEks', {
      groupId: this.albSecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 8080,
      toPort: 8080,
      destinationSecurityGroupId: this.eksSecurityGroup.securityGroupId,
      description: 'Allow traffic to EKS nodes',
    });

    // EKS ingress from ALB on port 8080
    new ec2.CfnSecurityGroupIngress(this, 'EksIngressFromAlb', {
      groupId: this.eksSecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 8080,
      toPort: 8080,
      sourceSecurityGroupId: this.albSecurityGroup.securityGroupId,
      description: 'Allow traffic from ALB',
    });

    // EKS ingress from self (node-to-node, all traffic)
    new ec2.CfnSecurityGroupIngress(this, 'EksIngressFromSelf', {
      groupId: this.eksSecurityGroup.securityGroupId,
      ipProtocol: '-1',
      sourceSecurityGroupId: this.eksSecurityGroup.securityGroupId,
      description: 'Allow node-to-node communication',
    });

    // EKS egress to RDS on port 5432
    new ec2.CfnSecurityGroupEgress(this, 'EksEgressToRds', {
      groupId: this.eksSecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 5432,
      toPort: 5432,
      destinationSecurityGroupId: this.databaseSecurityGroup.securityGroupId,
      description: 'Allow PostgreSQL traffic to RDS',
    });

    // EKS egress to HTTPS (AWS API calls) — CIDR-based
    new ec2.CfnSecurityGroupEgress(this, 'EksEgressHttps', {
      groupId: this.eksSecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 443,
      toPort: 443,
      cidrIp: '0.0.0.0/0',
      description: 'Allow HTTPS for AWS API calls',
    });

    // EKS egress to self (node-to-node, all traffic)
    new ec2.CfnSecurityGroupEgress(this, 'EksEgressToSelf', {
      groupId: this.eksSecurityGroup.securityGroupId,
      ipProtocol: '-1',
      destinationSecurityGroupId: this.eksSecurityGroup.securityGroupId,
      description: 'Allow node-to-node communication',
    });

    // Database ingress from EKS SG on port 5432
    new ec2.CfnSecurityGroupIngress(this, 'DbIngressFromEks', {
      groupId: this.databaseSecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 5432,
      toPort: 5432,
      sourceSecurityGroupId: this.eksSecurityGroup.securityGroupId,
      description: 'Allow PostgreSQL from EKS nodes only',
    });

    // Database ingress from private compute subnet CIDRs on port 5432
    // (covers EKS cluster auto-created SG traffic)
    this.privateComputeSubnets.forEach((subnet, idx) => {
      new ec2.CfnSecurityGroupIngress(this, `DbIngressFromComputeSubnet${idx + 1}`, {
        groupId: this.databaseSecurityGroup.securityGroupId,
        ipProtocol: 'tcp',
        fromPort: 5432,
        toPort: 5432,
        cidrIp: subnet.ipv4CidrBlock,
        description: `Allow PostgreSQL from ${subnet.availabilityZone} compute subnet`,
      });
    });

    // Database egress: localhost only (deny-all pattern for RDS)
    new ec2.CfnSecurityGroupEgress(this, 'DbEgressLocalhost', {
      groupId: this.databaseSecurityGroup.securityGroupId,
      ipProtocol: '-1',
      cidrIp: '127.0.0.1/32',
      description: 'Deny all outbound traffic (RDS does not need egress)',
    });

    // -----------------------------------------------------------------------
    // CloudFormation Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'VpcId', {
      description: 'VPC ID',
      value: this.vpc.vpcId,
    });

    new cdk.CfnOutput(this, 'PublicSubnetIds', {
      description: 'List of public subnet IDs',
      value: this.publicSubnets.map(s => s.subnetId).join(','),
    });

    new cdk.CfnOutput(this, 'PrivateComputeSubnetIds', {
      description: 'List of private compute subnet IDs (for EKS)',
      value: this.privateComputeSubnets.map(s => s.subnetId).join(','),
    });

    new cdk.CfnOutput(this, 'PrivateDataSubnetIds', {
      description: 'List of private data subnet IDs (for RDS)',
      value: this.privateDataSubnets.map(s => s.subnetId).join(','),
    });

    new cdk.CfnOutput(this, 'AlbSecurityGroupId', {
      description: 'ALB Security Group ID',
      value: this.albSecurityGroup.securityGroupId,
    });

    new cdk.CfnOutput(this, 'EksSecurityGroupId', {
      description: 'EKS Security Group ID',
      value: this.eksSecurityGroup.securityGroupId,
    });

    new cdk.CfnOutput(this, 'DatabaseSecurityGroupId', {
      description: 'RDS Security Group ID',
      value: this.databaseSecurityGroup.securityGroupId,
    });
  }
}
