import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import { WaApplicationContext } from './wa-agent';
import { lockProviderLogGroup } from './cdk-log-groups';

/** Tag that marks every resource of this application for the agent's context. */
const PROJECT_TAG = 'well-architected-agent-review';

/**
 * How the single-region application registers in the shared agent profile
 * (WaApplicationContextStack, deployed in the profile Region). The declared
 * requirements intentionally contradict what is deployed, so the agent can flag
 * the gap between the stated objectives and the actual architecture.
 */
export function singleRegionApplicationContext(workloadRegion: string): WaApplicationContext {
  return {
    projectTag: PROJECT_TAG,
    regions: [workloadRegion],
    title: 'Well-Architected Agent demo web application',
    applicationOverview: 'Customer-facing web application with a MySQL database and an S3 asset store.',
    architectureOverview:
      'Single EC2 web instance, single RDS MySQL instance, S3 asset bucket, credentials in Secrets Manager.',
    additionalContext: 'Business critical, RPO 5 minutes, RTO 2 hours, HA and DR in place',
  };
}

/**
 * WellArchitectedAgentReviewStack
 *
 * A DELIBERATELY NON-OPTIMIZED architecture. The point of this stack is to give
 * the Well-Architected Agent plenty of findings to generate recommendations against.
 * DO NOT use any of this as a template for production. Anti-patterns are called out
 * inline with "WA-ANTIPATTERN" comments so they are easy to find later.
 *
 * Pattern: a classic single-instance web app (EC2) backed by a single RDS database,
 * with an S3 bucket for assets.
 *
 * Note: the S3 bucket is intentionally kept PRIVATE (public access fully blocked) so
 * no data is ever exposed to the internet. Every other dimension is left
 * sub-optimal on purpose.
 */
export class WellArchitectedAgentReviewStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // -----------------------------------------------------------------------
    // Tagging
    // Tags are applied at stack level and inherited by every resource that
    // supports CloudFormation tags.
    //  - "project": identifies these resources so they can be filtered and
    //    selected for the Well-Architected Agent.
    // -----------------------------------------------------------------------
    cdk.Tags.of(this).add('project', PROJECT_TAG);

    // -----------------------------------------------------------------------
    // Networking
    // The VPC spans 2 AZs ONLY because RDS hard-requires a DB subnet group that
    // covers at least 2 Availability Zones -- otherwise CloudFormation refuses to
    // create the subnet group. This is an AWS platform requirement, not a design
    // choice: the database itself is still deployed single-AZ (multiAz: false),
    // so the "no failover" anti-pattern is preserved.
    // WA-ANTIPATTERN (Reliability): no NAT, everything in public subnets, no
    // private/isolated tiers. No separation between the app tier and data tier.
    // -----------------------------------------------------------------------
    const vpc = new ec2.Vpc(this, 'DemoVpc', {
      maxAzs: 2, // required by RDS subnet group; DB stays single-AZ below
      natGateways: 0,
      subnetConfiguration: [
        {
          name: 'public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
      ],
    });

    // -----------------------------------------------------------------------
    // Security group
    // Ingress is restricted to a trusted admin CIDR, and only to the ports we need
    // (SSH + the app's HTTP port). Set the admin CIDR via CDK context, e.g.
    //   npx cdk deploy -c adminCidr=203.0.113.4/32
    // If no CIDR is supplied we fall back to a non-routable placeholder so the
    // group is NOT open to the internet by default.
    // -----------------------------------------------------------------------
    const adminCidr =
      (this.node.tryGetContext('adminCidr') as string | undefined) ??
      '10.0.0.0/32'; // placeholder: effectively allows no public access

    const openSg = new ec2.SecurityGroup(this, 'OpenSecurityGroup', {
      vpc,
      description: 'Admin-restricted security group',
      allowAllOutbound: true,
    });
    // SSH from the trusted admin CIDR only.
    openSg.addIngressRule(
      ec2.Peer.ipv4(adminCidr),
      ec2.Port.tcp(22),
      'Allow SSH from the trusted admin CIDR',
    );
    // App HTTP port from the trusted admin CIDR only.
    openSg.addIngressRule(
      ec2.Peer.ipv4(adminCidr),
      ec2.Port.tcp(80),
      'Allow HTTP from the trusted admin CIDR',
    );

    // -----------------------------------------------------------------------
    // IAM role for the instance
    // WA-ANTIPATTERN (Security): grants AdministratorAccess to an EC2 instance
    // instead of a scoped, least-privilege policy.
    // -----------------------------------------------------------------------
    const instanceRole = new iam.Role(this, 'InstanceRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
    });
    instanceRole.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('AdministratorAccess'),
    );

    // -----------------------------------------------------------------------
    // Compute
    // WA-ANTIPATTERN (Reliability/Performance): a single, manually-sized EC2
    // instance with no Auto Scaling group, no load balancer, and no health
    // checks. It is also publicly addressable.
    // WA-ANTIPATTERN (Cost): oversized instance type for a demo workload.
    // WA-ANTIPATTERN (Security): no detailed monitoring, unencrypted root volume.
    // -----------------------------------------------------------------------
    const instance = new ec2.Instance(this, 'AppInstance', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      instanceType: ec2.InstanceType.of(
        ec2.InstanceClass.M5,
        ec2.InstanceSize.XLARGE, // WA-ANTIPATTERN: oversized / costly
      ),
      machineImage: ec2.MachineImage.latestAmazonLinux2(),
      securityGroup: openSg,
      role: instanceRole,
      associatePublicIpAddress: true, // WA-ANTIPATTERN: directly internet-facing
      blockDevices: [
        {
          deviceName: '/dev/xvda',
          volume: ec2.BlockDeviceVolume.ebs(50, {
            encrypted: false, // WA-ANTIPATTERN: unencrypted EBS volume
          }),
        },
      ],
    });

    // Explicitly disable EC2 API termination protection so the stack can always
    // be torn down / rolled back. The L2 Instance construct does not surface this
    // property, so set it on the underlying CfnInstance via the escape hatch.
    (instance.node.defaultChild as ec2.CfnInstance).disableApiTermination = false;

    // -----------------------------------------------------------------------
    // Database
    // WA-ANTIPATTERN (Reliability): single-AZ RDS, no read replica, no automated
    // backups, deletion protection off, destroyed on stack delete.
    // WA-ANTIPATTERN (Security): storage not encrypted.
    // The DB is not publicly accessible, and credentials are managed with a
    // generated AWS Secrets Manager secret (no plaintext password in the template).
    // -----------------------------------------------------------------------
    const database = new rds.DatabaseInstance(this, 'Database', {
      engine: rds.DatabaseInstanceEngine.mysql({
        version: rds.MysqlEngineVersion.VER_8_0,
      }),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      instanceType: ec2.InstanceType.of(
        ec2.InstanceClass.T3,
        ec2.InstanceSize.MICRO,
      ),
      securityGroups: [openSg],
      multiAz: false, // WA-ANTIPATTERN: single-AZ, no failover
      allocatedStorage: 20,
      storageEncrypted: false, // WA-ANTIPATTERN: unencrypted storage
      publiclyAccessible: false, // DB is not internet-facing
      backupRetention: cdk.Duration.days(0), // WA-ANTIPATTERN: backups disabled
      deletionProtection: false, // WA-ANTIPATTERN: no deletion protection
      // Master credentials are generated and stored in AWS Secrets Manager.
      // CDK creates the secret, generates a random password, and wires it into
      // the DB instance -- no plaintext secret ends up in the template.
      credentials: rds.Credentials.fromGeneratedSecret('admin'),
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      // WA-ANTIPATTERN: no Enhanced Monitoring / Performance Insights.
    });
    // The DB is reachable only from the app instance's security group.
    database.connections.allowDefaultPortFrom(
      openSg,
      'Allow DB access from the app security group only',
    );

    // -----------------------------------------------------------------------
    // Storage
    // S3 bucket for assets. Kept PRIVATE on purpose (public access fully blocked)
    // so no data is exposed to the internet.
    // WA-ANTIPATTERN (Reliability/Security): still no versioning, no default
    // encryption config, no lifecycle rules, no access logging. Auto-deleted on
    // stack removal.
    // -----------------------------------------------------------------------
    const assetBucket = new s3.Bucket(this, 'AssetBucket', {
      // Public access stays blocked -- this is the one thing we keep correct.
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      publicReadAccess: false,
      versioned: false, // WA-ANTIPATTERN: no object versioning
      enforceSSL: false, // WA-ANTIPATTERN: does not require TLS for access
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true, // WA-ANTIPATTERN: data loss on stack delete
      // WA-ANTIPATTERN: no serverAccessLogsBucket, no lifecycle rules.
    });

    // -----------------------------------------------------------------------
    // Log group for the CDK auto-delete-objects Lambda
    // `autoDeleteObjects` adds a CDK-managed Lambda that empties the bucket on
    // stack deletion. Left alone, it creates its own log group at runtime, which
    // survives `cdk destroy` as an orphan, and can even recreate it after the
    // stack deleted it (late log delivery). See lib/cdk-log-groups.ts.
    // Lambda names are regional; the suffix keeps the name readable per deployment.
    // -----------------------------------------------------------------------
    lockProviderLogGroup(
      this,
      'Custom::S3AutoDeleteObjectsCustomResourceProvider',
      `WAAgentReview-S3AutoDeleteObjects-${this.region}`,
      'AutoDeleteObjectsLogGroup',
    );

    // The Well-Architected Agent is not part of this stack: the shared profile
    // stack and this application's context stack (lib/wa-agent.ts) review it.

    // -----------------------------------------------------------------------
    // Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'InstancePublicIp', {
      value: instance.instancePublicIp,
      description: 'Public IP of the single app instance',
    });
    new cdk.CfnOutput(this, 'DatabaseEndpoint', {
      value: database.dbInstanceEndpointAddress,
      description: 'RDS endpoint (private, reachable only from the app security group)',
    });
    if (database.secret) {
      new cdk.CfnOutput(this, 'DatabaseSecretArn', {
        value: database.secret.secretArn,
        description:
          'Secrets Manager secret ARN holding the generated DB credentials',
      });
    }
    new cdk.CfnOutput(this, 'AssetBucketName', {
      value: assetBucket.bucketName,
      description: 'Private asset bucket name',
    });
  }
}
