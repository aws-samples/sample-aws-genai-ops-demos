import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  StackProps,
  Tags,
  aws_autoscaling as autoscaling,
  aws_certificatemanager as acm,
  aws_dynamodb as dynamodb,
  aws_ec2 as ec2,
  aws_elasticloadbalancingv2 as elbv2,
  aws_elasticloadbalancingv2_targets as elbv2Targets,
  aws_iam as iam,
  aws_resourcegroups as resourcegroups,
  aws_route53 as route53,
} from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { AppConfig } from './config';
import { renderUserData } from './userdata';
import { addWellArchitectedAgent, assertAgentProfileRegion } from './wa-agent';
import { MULTI_REGION_AGENT_GOALS } from './multi-region-agent';
import { lockProviderLogGroup } from './cdk-log-groups';

export type RegionRole = 'primary' | 'secondary';

export interface RegionStackProps extends StackProps {
  cfg: AppConfig;
  roleLabel: RegionRole;
  vpcCidr: string;
}

/**
 * One region of the active/active web app:
 *   VPC (2 public + 2 private subnets, no NAT) -> gateway endpoints (S3, DynamoDB)
 *   -> SSM interface endpoints -> internet-facing ALB (admin CIDR + Route 53 health
 *   checkers only) -> private web tier (multi-AZ ASG and/or single instance).
 *
 * Remaining gaps are marked inline with `WA-ANTIPATTERN` so the Well-Architected
 * Agent findings can be checked against a known list.
 * The primary stack also owns the DynamoDB global table (replica in the
 * secondary region), the Well-Architected Agent profile that reviews both
 * Regions and, optionally, the global Resilience Hub IAM role.
 */
export class RegionStack extends Stack {
  public readonly alb: elbv2.ApplicationLoadBalancer;
  public readonly targetGroup: elbv2.ApplicationTargetGroup;

  constructor(scope: Construct, id: string, props: RegionStackProps) {
    super(scope, id, props);

    // Feature flags of this variant, set on the stack so they do not change the
    // single-region stack that shares cdk.json. Must run before any child exists.
    //  - restrictDefaultSecurityGroup: strip all rules from the VPC default SG.
    //  - albDualstackWithoutPublicIpv4SecurityGroupRulesDefault: current ALB default.
    this.node.setContext('@aws-cdk/aws-ec2:restrictDefaultSecurityGroup', true);
    this.node.setContext('@aws-cdk/aws-elasticloadbalancingV2:albDualstackWithoutPublicIpv4SecurityGroupRulesDefault', true);

    const { cfg, roleLabel, vpcCidr } = props;
    const name = `${cfg.project}-${roleLabel}`;
    const region = Stack.of(this).region;
    const enableHttps = cfg.appDomainName !== '';

    // ------------------------------------------------------------------
    // DynamoDB global table (primary stack only)
    // Active/active: both replicas accept local writes, DynamoDB replicates
    // bidirectionally. Streams are enabled automatically by TableV2.
    // ------------------------------------------------------------------
    if (roleLabel === 'primary') {
      const table = new dynamodb.TableV2(this, 'GlobalTable', {
        tableName: cfg.ddbTableName,
        partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
        billing: dynamodb.Billing.onDemand(),
        // Encryption at rest declared explicitly (SEC 04): AWS owned key, the
        // DynamoDB default at no extra charge. It renders as SSEEnabled: false in
        // the template, which means "AWS owned key", not "unencrypted".
        encryption: dynamodb.TableEncryptionV2.dynamoOwnedKey(),
        pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
        replicas: [
          {
            region: cfg.secondaryRegion,
            pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
          },
        ],
        // Demo lab: `cdk destroy` removes the table and its replica.
        removalPolicy: RemovalPolicy.DESTROY,
        deletionProtection: false,
      });
      Tags.of(table).add('Name', `${cfg.project}-global-table`);

      new CfnOutput(this, 'DynamoDbTableName', {
        description: 'DynamoDB global table name',
        value: table.tableName,
      });

      // Well-Architected Agent profile, goals and application context, shared with
      // the single-region variant (lib/wa-agent.ts). Primary Region only; the
      // profile analyzes both Regions. Also emits the AgentProfileArn output.
      assertAgentProfileRegion(cfg.primaryRegion);
      addWellArchitectedAgent(this, {
        projectTag: cfg.projectTag,
        // MultiRegion infix: never collides with the single-region variant's roles.
        roleNamePrefix: 'WAAgentMultiRegion',
        displayName: 'Well-Architected Agent Review (multi-region)',
        description: 'Reviews the multi-region active/active demo workload',
        regions: [cfg.primaryRegion, cfg.secondaryRegion],
        goals: MULTI_REGION_AGENT_GOALS,
        context: {
          title: 'Multi-region active/active web application',
          applicationOverview:
            'Customer-facing web application served from two AWS Regions, with a visit counter in a DynamoDB global table.',
          architectureOverview:
            'In each Region: internet-facing Application Load Balancer, multi-AZ Auto Scaling group in private subnets, VPC endpoints, no NAT gateway. DynamoDB global table replicated between the two Regions.',
          // Declared on purpose beyond what is deployed (no automatic Regional
          // failover, no alarms), so the agent can flag the gap.
          additionalContext:
            'Business critical, RPO 1 minute, RTO 15 minutes, active/active in two Regions with automatic Regional failover, failures detected and alerted within 5 minutes',
        },
      });
    }

    // ------------------------------------------------------------------
    // Networking: 2 AZs, public subnets for the ALB, private (isolated)
    // subnets for the web tier and interface endpoints. No NAT gateway:
    // egress to AWS services goes only through VPC endpoints.
    // ------------------------------------------------------------------
    const vpc = new ec2.Vpc(this, 'Vpc', {
      vpcName: `${name}-vpc`,
      ipAddresses: ec2.IpAddresses.cidr(vpcCidr),
      maxAzs: 2,
      natGateways: 0,
      enableDnsHostnames: true,
      enableDnsSupport: true,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24, mapPublicIpOnLaunch: true },
        { name: 'private', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
    });
    const privateSubnets: ec2.SubnetSelection = { subnetType: ec2.SubnetType.PRIVATE_ISOLATED };

    // Log group for the CDK Lambda that strips the rules of the VPC default
    // security group: stack-managed, no logs:CreateLogGroup, so `cdk destroy`
    // leaves no orphan (same fix as the S3 auto-delete Lambda, lib/cdk-log-groups.ts).
    lockProviderLogGroup(
      this,
      'Custom::VpcRestrictDefaultSGCustomResourceProvider',
      `WAAgentReviewMR-RestrictDefaultSG-${region}`,
      'RestrictDefaultSgLogGroup',
    );

    // S3 gateway endpoint: dnf reaches the AL2023 repos (hosted in S3) without NAT.
    const s3Endpoint = vpc.addGatewayEndpoint('S3Endpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
      subnets: [privateSubnets],
    });
    // DynamoDB gateway endpoint: the app writes to its local replica without leaving the VPC.
    const ddbEndpoint = vpc.addGatewayEndpoint('DynamoDbEndpoint', {
      service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
      subnets: [privateSubnets],
    });

    // SSM interface endpoints: Session Manager access with no SSH and no public IP.
    const endpointsSg = new ec2.SecurityGroup(this, 'EndpointsSg', {
      vpc,
      securityGroupName: `${name}-endpoints-sg`,
      description: 'HTTPS from within the VPC to interface endpoints',
      allowAllOutbound: true,
    });
    endpointsSg.addIngressRule(ec2.Peer.ipv4(vpcCidr), ec2.Port.tcp(443), 'HTTPS from VPC');

    const interfaceEndpoints = [
      { id: 'SsmEndpoint', service: ec2.InterfaceVpcEndpointAwsService.SSM },
      { id: 'SsmMessagesEndpoint', service: ec2.InterfaceVpcEndpointAwsService.SSM_MESSAGES },
      { id: 'Ec2MessagesEndpoint', service: ec2.InterfaceVpcEndpointAwsService.EC2_MESSAGES },
    ].map(({ id: epId, service }) =>
      vpc.addInterfaceEndpoint(epId, {
        service,
        subnets: privateSubnets,
        securityGroups: [endpointsSg],
        privateDnsEnabled: true,
        open: false,
      }),
    );
    const allEndpoints = [s3Endpoint, ddbEndpoint, ...interfaceEndpoints];

    // ------------------------------------------------------------------
    // Security groups
    // ------------------------------------------------------------------
    // AWS-managed prefix list with the current Route 53 health checker ranges
    // for this region: lets endpoint health checks reach the ALB while real
    // traffic stays restricted to the admin CIDR. AWS keeps the ranges up to date.
    const r53HealthCheckers = ec2.PrefixList.fromLookup(this, 'Route53HealthCheckers', {
      prefixListName: `com.amazonaws.${region}.route53-healthchecks`,
    });

    const albSg = new ec2.SecurityGroup(this, 'AlbSg', {
      vpc,
      securityGroupName: `${name}-alb-sg`,
      description: 'ALB access from the admin CIDR and Route 53 health checkers',
      allowAllOutbound: true,
    });
    const albPorts = enableHttps ? [80, 443] : [80];
    for (const port of albPorts) {
      const proto = port === 443 ? 'HTTPS' : 'HTTP';
      albSg.addIngressRule(ec2.Peer.ipv4(cfg.adminCidr), ec2.Port.tcp(port), `${proto} from admin CIDR`);
      albSg.addIngressRule(
        ec2.Peer.prefixList(r53HealthCheckers.prefixListId),
        ec2.Port.tcp(port),
        `${proto} from Route 53 health checkers (managed prefix list)`,
      );
    }

    // Web tier SG: only accepts traffic from the ALB.
    const ec2Sg = new ec2.SecurityGroup(this, 'Ec2Sg', {
      vpc,
      securityGroupName: `${name}-ec2-sg`,
      description: 'EC2 access only from the ALB',
      allowAllOutbound: true,
    });
    ec2Sg.addIngressRule(albSg, ec2.Port.tcp(80), 'HTTP from ALB');

    // ------------------------------------------------------------------
    // IAM: Session Manager + least-privilege access to the local replica only.
    // ------------------------------------------------------------------
    const role = new iam.Role(this, 'Ec2Role', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore')],
    });
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'dynamodb:PutItem',
          'dynamodb:UpdateItem',
          'dynamodb:GetItem',
          'dynamodb:Query',
          'dynamodb:DescribeTable',
        ],
        resources: [
          Stack.of(this).formatArn({ service: 'dynamodb', resource: 'table', resourceName: cfg.ddbTableName }),
        ],
      }),
    );

    // ------------------------------------------------------------------
    // Compute
    // ------------------------------------------------------------------
    // Latest AL2023 AMI, resolved by CloudFormation from the public SSM parameter.
    const ami = ec2.MachineImage.latestAmazonLinux2023();
    const instanceType = new ec2.InstanceType(cfg.instanceType);
    const userData = ec2.UserData.custom(renderUserData(region, cfg.ddbTableName));
    // Encrypted gp3 root volume (SEC 04), declared explicitly instead of relying on
    // the account-level EBS encryption-by-default setting.
    const rootVolume: ec2.BlockDevice[] = [
      {
        deviceName: '/dev/xvda',
        volume: ec2.BlockDeviceVolume.ebs(8, { encrypted: true, volumeType: ec2.EbsDeviceVolumeType.GP3 }),
      },
    ];

    // ------------------------------------------------------------------
    // Application Load Balancer
    // ------------------------------------------------------------------
    this.alb = new elbv2.ApplicationLoadBalancer(this, 'Alb', {
      vpc,
      loadBalancerName: `${name}-alb`,
      internetFacing: true,
      securityGroup: albSg,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
    });
    // ARC zonal shift enabled (lets you evacuate an impaired AZ).
    this.alb.setAttribute('zonal_shift.config.enabled', 'true');
    // WA-ANTIPATTERN (Security): no ALB access logs, so requests cannot be audited.
    // WA-ANTIPATTERN (Security): no AWS WAF web ACL associated with the internet-facing ALB.

    this.targetGroup = new elbv2.ApplicationTargetGroup(this, 'TargetGroup', {
      vpc,
      targetGroupName: `${name}-tg`,
      port: 80,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targetType: elbv2.TargetType.INSTANCE,
      healthCheck: {
        path: '/',
        healthyHttpCodes: '200',
        interval: Duration.seconds(30),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 3,
      },
    });

    // Optional single instance, off by default (keepSingleInstance=true), for a
    // single-instance baseline with useAsg=false.
    // WA-ANTIPATTERN (Resilience, only when enabled): one instance in one AZ is a
    // single point of failure for the region.
    let instance: ec2.Instance | undefined;
    if (cfg.keepSingleInstance) {
      instance = new ec2.Instance(this, 'Web', {
        vpc,
        vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED, availabilityZones: [vpc.availabilityZones[0]] },
        instanceType,
        machineImage: ami,
        securityGroup: ec2Sg,
        role,
        userData,
        blockDevices: rootVolume,
        requireImdsv2: true,
        associatePublicIpAddress: false,
      });
      Tags.of(instance).add('Name', `${name}-web`);
      allEndpoints.forEach((ep) => instance!.node.addDependency(ep));
      this.targetGroup.addTarget(new elbv2Targets.InstanceTarget(instance, 80));
    }

    // Auto Scaling group. Its shape differs per Region on purpose (config.ts, `web`):
    //  - primary (default): confined to ONE Availability Zone.
    //    WA-ANTIPATTERN (Resilience): zonal single point of failure. An AZ
    //    impairment takes down the whole web tier of the primary Region. The ALB
    //    still spans two AZs and has ARC zonal shift enabled, but shifting away
    //    from the only AZ with instances leaves no healthy target: the control is
    //    present but cannot help.
    //  - secondary (default): fixed capacity of ONE instance (min = max = 1).
    //    WA-ANTIPATTERN (Resilience / Performance): no static stability. If the
    //    primary Region fails, a single instance that cannot scale must absorb all
    //    the traffic, so the declared active/active failover within an RTO of
    //    15 minutes is not achievable.
    // Override with -c primaryWebAzCount=2 / -c secondaryAsgMaxSize=4 (and the
    // other keys in config.ts) to deploy the resilient shape instead.
    const webShape = cfg.web[roleLabel];
    const webAzs = vpc.availabilityZones.slice(0, webShape.azCount);
    let asg: autoscaling.AutoScalingGroup | undefined;
    if (cfg.useAsg) {
      const launchTemplate = new ec2.LaunchTemplate(this, 'WebLaunchTemplate', {
        launchTemplateName: `${name}-web-lt`,
        machineImage: ami,
        instanceType,
        securityGroup: ec2Sg,
        role,
        userData,
        blockDevices: rootVolume,
        requireImdsv2: true,
      });
      Tags.of(launchTemplate).add('Name', `${name}-web-lt`);
      // Explicit tag specifications for launched instances and their volumes
      // (CDK only tags the launch template itself). The `project` tag scopes the
      // agent's application context and the Resilience Hub resource groups.
      const launchedTags = [
        { key: 'Name', value: `${name}-web` },
        { key: 'project', value: cfg.projectTag },
        { key: 'Env', value: 'demo' },
        { key: 'ManagedBy', value: 'cdk' },
      ].map(({ key, value }) => ({ Key: key, Value: value }));
      (launchTemplate.node.defaultChild as ec2.CfnLaunchTemplate).addPropertyOverride(
        'LaunchTemplateData.TagSpecifications',
        ['instance', 'volume'].map((resourceType) => ({ ResourceType: resourceType, Tags: launchedTags })),
      );

      asg = new autoscaling.AutoScalingGroup(this, 'WebAsg', {
        autoScalingGroupName: `${name}-asg`,
        vpc,
        vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED, availabilityZones: webAzs },
        launchTemplate,
        minCapacity: webShape.asgMinSize,
        maxCapacity: webShape.asgMaxSize,
        healthChecks: autoscaling.HealthChecks.withAdditionalChecks({
          additionalTypes: [autoscaling.AdditionalHealthCheckType.ELB],
          gracePeriod: Duration.seconds(300),
        }),
        // Rolling replacement on launch template changes, keeping at least half of
        // the fleet in service. CloudFormation requires MinInstancesInService to be
        // lower than MaxSize, so a one-instance group (secondary) replaces with an
        // outage, which is part of the gap described above.
        updatePolicy: autoscaling.UpdatePolicy.rollingUpdate({
          maxBatchSize: 1,
          minInstancesInService: Math.min(
            Math.max(1, Math.ceil(webShape.asgMinSize / 2)),
            webShape.asgMaxSize - 1,
          ),
          pauseTime: Duration.minutes(3),
        }),
      });
      allEndpoints.forEach((ep) => asg!.node.addDependency(ep));
      asg.attachToApplicationTargetGroup(this.targetGroup);
      // WA-ANTIPATTERN (Performance / Cost Optimization): no scaling policy. The group
      // stays at minCapacity and never scales out to maxCapacity on load, nor in.
    }

    // ------------------------------------------------------------------
    // Listeners. `open: false` everywhere: ingress is managed explicitly on the
    // ALB SG (admin CIDR + Route 53 health checkers), never 0.0.0.0/0.
    // ------------------------------------------------------------------
    if (enableHttps) {
      // Port 80 redirects to 443. A 301 still counts as Healthy for Route 53
      // endpoint health checks (2xx/3xx), so port-80 health checks keep working.
      this.alb.addListener('Http', {
        port: 80,
        protocol: elbv2.ApplicationProtocol.HTTP,
        open: false,
        defaultAction: elbv2.ListenerAction.redirect({ port: '443', protocol: 'HTTPS', permanent: true }),
      });

      // ACM DNS validation tokens are valid in every Region, so only the primary
      // stack writes the CNAMEs; the secondary certificate validates against the
      // same records (the secondary stack is deployed after the primary).
      const writeRecords = roleLabel === 'primary' && cfg.createCertValidationRecords;
      const validation = writeRecords
        ? acm.CertificateValidation.fromDns(
            route53.HostedZone.fromLookup(this, 'PublicZone', { domainName: cfg.route53ZoneName, privateZone: false }),
          )
        : acm.CertificateValidation.fromDns();

      // Public certificates used with an integrated service (this ALB) are issued at no cost.
      const certificate = new acm.Certificate(this, 'Certificate', {
        domainName: cfg.appDomainName,
        subjectAlternativeNames: cfg.appDomainAltNames.length ? cfg.appDomainAltNames : undefined,
        validation,
      });
      // Higher priority than the default Name tag the Certificate construct sets.
      Tags.of(certificate).add('Name', `${name}-cert`, { priority: 300 });

      this.alb.addListener('Https', {
        port: 443,
        protocol: elbv2.ApplicationProtocol.HTTPS,
        open: false,
        sslPolicy: elbv2.SslPolicy.RECOMMENDED_TLS, // ELBSecurityPolicy-TLS13-1-2-2021-06
        certificates: [certificate],
        defaultTargetGroups: [this.targetGroup],
      });
    } else {
      // WA-ANTIPATTERN (Security, default when appDomainName is empty): plain HTTP,
      // traffic to the ALB is not encrypted in transit.
      this.alb.addListener('Http', {
        port: 80,
        protocol: elbv2.ApplicationProtocol.HTTP,
        open: false,
        defaultTargetGroups: [this.targetGroup],
      });
    }

    // ------------------------------------------------------------------
    // AWS Resilience Hub (optional)
    // The IAM role is GLOBAL: one role for the account, created in the primary
    // stack, trusted by Resilience Hub in BOTH regions. The tag-based resource
    // groups are regional and act as the discovery source in each region.
    // ------------------------------------------------------------------
    if (cfg.enableResilienceHub) {
      const group = new resourcegroups.CfnGroup(this, 'ResilienceGroup', {
        name: `${cfg.project}-resilience`,
        resourceQuery: {
          type: 'TAG_FILTERS_1_0',
          query: {
            resourceTypeFilters: ['AWS::AllSupported'],
            tagFilters: [{ key: 'project', values: [cfg.projectTag] }],
          },
        },
      });
      new CfnOutput(this, 'ResilienceHubResourceGroupArn', {
        description: 'Tag-based resource group (project=<projectTag>) to register as Resilience Hub input source',
        value: group.attrArn,
      });

      if (roleLabel === 'primary') {
        const account = Stack.of(this).account;
        const rhRole = new iam.Role(this, 'ResilienceHubRole', {
          assumedBy: new iam.ServicePrincipal('resiliencehub.amazonaws.com').withConditions({
            StringEquals: { 'aws:SourceAccount': account },
            ArnLike: {
              'aws:SourceArn': [
                `arn:aws:resiliencehub:${cfg.primaryRegion}:${account}:*`,
                `arn:aws:resiliencehub:${cfg.secondaryRegion}:${account}:*`,
              ],
            },
          }),
          // Next generation Resilience Hub managed policies.
          managedPolicies: [
            iam.ManagedPolicy.fromAwsManagedPolicyName('AWSResilienceHubV2AssessmentExecutionPolicy'),
            iam.ManagedPolicy.fromAwsManagedPolicyName('AWSResilienceHubResilienceTestingPolicy'),
          ],
        });
        new CfnOutput(this, 'ResilienceHubRoleArn', {
          description: 'IAM role for Resilience Hub discovery/assessment',
          value: rhRole.roleArn,
        });
      }
    }

    // ------------------------------------------------------------------
    // Outputs
    // ------------------------------------------------------------------
    // WA-ANTIPATTERN (Resilience): cross-region routing is not codified. No Route 53
    // records or health checks are created, so regional failover depends on DNS
    // you configure by hand (see ARCHITECTURE.md). This contradicts the automatic
    // failover declared in the agent's application context.
    // WA-ANTIPATTERN (Resilience): no CloudWatch alarms (5xx, unhealthy hosts,
    // latency) and no notifications; failures are only visible on the dashboard.
    new CfnOutput(this, 'AlbDnsName', {
      description: `${roleLabel} ALB DNS name (Route 53 alias target)`,
      value: this.alb.loadBalancerDnsName,
    });
    new CfnOutput(this, 'AlbZoneId', {
      description: `${roleLabel} ALB hosted zone ID (Route 53 alias records)`,
      value: this.alb.loadBalancerCanonicalHostedZoneId,
    });
    // CloudWatch dimension values, read by the deploy script and passed to the
    // dashboard stack as --context (no cross-Region references).
    new CfnOutput(this, 'AlbFullName', {
      description: `${roleLabel} ALB CloudWatch dimension (app/<name>/<id>)`,
      value: this.alb.loadBalancerFullName,
    });
    new CfnOutput(this, 'TargetGroupFullName', {
      description: `${roleLabel} target group CloudWatch dimension (targetgroup/<name>/<id>)`,
      value: this.targetGroup.targetGroupFullName,
    });
    if (instance) {
      new CfnOutput(this, 'InstanceId', { description: `${roleLabel} single EC2 instance ID`, value: instance.instanceId });
    }
    if (asg) {
      new CfnOutput(this, 'AsgName', { description: `${roleLabel} Auto Scaling Group name`, value: asg.autoScalingGroupName });
    }
  }
}
