import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

/**
 * The self-managed Active Directory the file system joins: one VPC (one AZ, one NAT gateway)
 * and one Windows Server domain controller promoted by its own user data, plus the
 * service-account secret FSx authenticates with.
 *
 * Self-managed rather than AWS Managed Microsoft AD because the demo's headline scenario
 * rotates the service-account password FSx holds: Managed AD gives FSx no password to get
 * wrong. It is also cheaper (a t3.medium against $0.12/h).
 *
 * The user data runs twice (persisted): first boot installs AD DS and promotes the forest
 * (reboot), second boot creates the service account and writes the SSM parameter deploy-all
 * waits for before creating the file system. FSx joins the domain at creation and fails
 * after 30 minutes if the domain is not reachable, so the wait is not optional.
 *
 * Demo shortcuts, stated here and in the README: the service account is a Domain Admin
 * (the FSx documentation lists the delegated permissions to use instead), and the domain
 * controller's security group admits everything from inside the VPC (the AD port list is
 * long; nothing outside the VPC can reach it).
 */
export interface DirectoryStackProps extends cdk.StackProps {
  projectName: string;
}

/** What the other stacks need to know about the directory. */
export interface DirectoryFacts {
  readonly domainName: string;
  readonly netbiosName: string;
  /** The domain controller's private IP, also the DNS server FSx is given. */
  readonly dnsIp: string;
  /** {"username": ..., "password": ...}: FSx's service account; the Lab reads it to revert the credentials scenario. */
  readonly serviceAccountSecret: secretsmanager.ISecret;
  readonly domainController: ec2.Instance;
  /** SSM parameter the domain controller writes when the forest and the service account exist. */
  readonly readyParameterName: string;
}

const DOMAIN_NAME = 'corp.example.com';
const NETBIOS_NAME = 'CORP';
const SERVICE_ACCOUNT = 'FSxService';

export class DirectoryStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;
  public readonly directory: DirectoryFacts;

  constructor(scope: Construct, id: string, props: DirectoryStackProps) {
    super(scope, id, props);
    const { projectName } = props;

    // One AZ is enough: the file system is Single-AZ on purpose (dimension 1 of the review).
    this.vpc = new ec2.Vpc(this, 'Vpc', {
      ipAddresses: ec2.IpAddresses.cidr('10.0.0.0/16'),
      maxAzs: 1,
      natGateways: 1,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: 'private', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 24 },
      ],
    });
    const privateSubnet = this.vpc.privateSubnets[0];
    // FSx needs the DNS server IP as a literal at creation: give the domain controller a
    // fixed address in its subnet (CDK computes subnet CIDRs at synth time, so this is a string).
    const dnsIp = hostInCidr(privateSubnet.ipv4CidrBlock, 10);

    const serviceAccountSecret = new secretsmanager.Secret(this, 'ServiceAccountSecret', {
      description: `Active Directory service account FSx uses for ${projectName} ({"username","password"}, the format the AWSSupport-ValidateFSxWindowsADConfig runbook expects)`,
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: SERVICE_ACCOUNT }),
        generateStringKey: 'password',
        passwordLength: 24,
        requireEachIncludedType: true,
        // Keep the password safe inside PowerShell, JSON and an FSx API call.
        excludeCharacters: '"\'`$\\/&|<>;,{}[]()#%^*~ ',
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const readyParameterName = `/${projectName}/directory/ready`;

    const securityGroup = new ec2.SecurityGroup(this, 'DomainControllerSg', {
      vpc: this.vpc,
      description: 'Domain controller: reachable from inside the VPC only (FSx joins and authenticates against it)',
      allowAllOutbound: true,
    });
    securityGroup.addIngressRule(ec2.Peer.ipv4(this.vpc.vpcCidrBlock), ec2.Port.allTraffic(), 'Active Directory from inside the VPC');

    const role = new iam.Role(this, 'DomainControllerRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore')],
    });
    serviceAccountSecret.grantRead(role);
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['ssm:PutParameter'],
      resources: [this.formatArn({ service: 'ssm', resource: 'parameter', resourceName: readyParameterName.replace(/^\//, '') })],
    }));

    const userData = ec2.UserData.forWindows({ persist: true });
    userData.addCommands(...promotionScript({
      domainName: DOMAIN_NAME,
      netbiosName: NETBIOS_NAME,
      secretArn: serviceAccountSecret.secretArn,
      readyParameterName,
      region: this.region,
    }));

    const domainController = new ec2.Instance(this, 'DomainController', {
      vpc: this.vpc,
      vpcSubnets: { subnets: [privateSubnet] },
      privateIpAddress: dnsIp,
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM),
      machineImage: ec2.MachineImage.latestWindows(ec2.WindowsVersion.WINDOWS_SERVER_2022_ENGLISH_FULL_BASE),
      securityGroup,
      role,
      userData,
      requireImdsv2: true,
      blockDevices: [{ deviceName: '/dev/sda1', volume: ec2.BlockDeviceVolume.ebs(30, { volumeType: ec2.EbsDeviceVolumeType.GP3, encrypted: true }) }],
    });
    cdk.Tags.of(domainController).add('Name', `${projectName}-domain-controller`);

    this.directory = { domainName: DOMAIN_NAME, netbiosName: NETBIOS_NAME, dnsIp, serviceAccountSecret, domainController, readyParameterName };

    new cdk.CfnOutput(this, 'VpcId', { value: this.vpc.vpcId });
    new cdk.CfnOutput(this, 'DomainControllerInstanceId', { value: domainController.instanceId, description: 'Domain controller (Session Manager for a shell; no key pair)' });
    new cdk.CfnOutput(this, 'DomainName', { value: DOMAIN_NAME });
    new cdk.CfnOutput(this, 'DnsIp', { value: dnsIp, description: 'Domain controller private IP, the DNS server FSx uses' });
    new cdk.CfnOutput(this, 'ServiceAccountSecretArn', { value: serviceAccountSecret.secretArn });
    new cdk.CfnOutput(this, 'ReadyParameterName', { value: readyParameterName, description: 'SSM parameter set to "ready" once the domain and the service account exist' });
  }
}

/** Host address n inside a synth-time CIDR string such as 10.0.1.0/24. */
function hostInCidr(cidr: string, n: number): string {
  if (cdk.Token.isUnresolved(cidr)) {
    throw new Error('The private subnet CIDR must be known at synth time to place the domain controller');
  }
  const [network] = cidr.split('/');
  const octets = network.split('.').map(Number);
  octets[3] += n;
  return octets.join('.');
}

/**
 * The domain controller's user data (PowerShell, persisted so it runs on every boot).
 * Phase 1: install AD DS, promote the forest, reboot. Phase 2: wait for AD Web Services,
 * create the service account, publish "ready". AWS Tools for PowerShell ship with the AMI.
 */
function promotionScript(p: { domainName: string; netbiosName: string; secretArn: string; readyParameterName: string; region: string }): string[] {
  return [
    `$ErrorActionPreference = 'Stop'`,
    `Start-Transcript -Path C:\\dc-setup.log -Append`,
    `Import-Module AWSPowerShell`,
    `$secret = (Get-SECSecretValue -SecretId '${p.secretArn}' -Region '${p.region}').SecretString | ConvertFrom-Json`,
    `$password = ConvertTo-SecureString $secret.password -AsPlainText -Force`,
    `$marker = 'C:\\dc-promoted.marker'`,
    `if (-not (Test-Path $marker)) {`,
    `  Install-WindowsFeature AD-Domain-Services -IncludeManagementTools`,
    `  New-Item $marker -ItemType File | Out-Null`,
    `  Install-ADDSForest -DomainName '${p.domainName}' -DomainNetbiosName '${p.netbiosName}' -SafeModeAdministratorPassword $password -InstallDns -Force`,
    `  exit`,
    `}`,
    `$deadline = (Get-Date).AddMinutes(20)`,
    `while ((Get-Date) -lt $deadline) { try { Get-ADDomain -ErrorAction Stop | Out-Null; break } catch { Start-Sleep -Seconds 15 } }`,
    `if (-not (Get-ADUser -Filter "SamAccountName -eq '$($secret.username)'")) {`,
    `  New-ADUser -Name $secret.username -SamAccountName $secret.username -AccountPassword $password -Enabled $true -PasswordNeverExpires $true -Description 'Service account Amazon FSx uses to join the domain'`,
    `  Add-ADGroupMember -Identity 'Domain Admins' -Members $secret.username`,
    `}`,
    `Write-SSMParameter -Name '${p.readyParameterName}' -Value 'ready' -Type String -Overwrite $true -Region '${p.region}'`,
    `Stop-Transcript`,
  ];
}
