/**
 * The four stacks synthesize and keep the promises the README makes: one tracked stack,
 * region-suffixed ids, a Single-AZ 8 MB/s file system whose AD password is a dynamic
 * reference (never a literal), exactly one AWS/FSx alarm on the file system, a Lab site
 * that denies everything without a password.
 */
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { AgentSpaceStack } from '../lib/agent-space-stack';
import { DirectoryStack } from '../lib/directory-stack';
import { FileSystemStack } from '../lib/file-system-stack';
import { LabStack } from '../lib/lab-stack';

const TRACKING = '(uksb-do9bhieqqh)(tag:fsx-windows-sla-review,resilience)';

function synth(labPassword: string, region = 'eu-west-1') {
  const app = new cdk.App();
  const env = { region };
  const projectName = 'fsx-sla-review';
  const agentSpace = new AgentSpaceStack(app, `FsxSlaReviewAgentSpace-${region}`, { env, projectName, description: 'Agent Space' });
  const directory = new DirectoryStack(app, `FsxSlaReviewDirectory-${region}`, { env, projectName, description: 'Directory' });
  const fileSystem = new FileSystemStack(app, `FsxSlaReviewFileSystem-${region}`, {
    env, projectName, vpc: directory.vpc, directory: directory.directory,
    webhookUrl: 'https://example.com/webhook', webhookSecretArn: 'arn:aws:secretsmanager:eu-west-1:123456789012:secret:test-AbCdEf',
    webhookSecretRegion: region, description: `FSx for Windows SLA review ${TRACKING}`,
  });
  const lab = new LabStack(app, `FsxSlaReviewLab-${region}`, {
    env, projectName, fileSystem: fileSystem.fileSystem, directory: directory.directory, alarms: fileSystem.alarms,
    triggerFunctionName: fileSystem.triggerFunctionName, devOpsAgentRegion: region, devOpsAgentSpaceId: 'space-1',
    labUser: 'presenter', labPassword, description: 'Lab',
  });
  return { app, agentSpace, directory, fileSystem, lab };
}

describe('FSx for Windows SLA review stacks', () => {
  const { app, agentSpace, directory, fileSystem, lab } = synth('s3cret');
  const assembly = app.synth();

  test('four region-suffixed stacks, tracking on the main stack only', () => {
    const names = assembly.stacks.map(s => s.stackName);
    expect(names).toEqual(expect.arrayContaining(['FsxSlaReviewAgentSpace-eu-west-1', 'FsxSlaReviewDirectory-eu-west-1', 'FsxSlaReviewFileSystem-eu-west-1', 'FsxSlaReviewLab-eu-west-1']));
    const tracked = assembly.stacks.filter(s => JSON.stringify(s.template.Description ?? '').includes('uksb-do9bhieqqh'));
    expect(tracked.map(s => s.stackName)).toEqual(['FsxSlaReviewFileSystem-eu-west-1']);
  });

  test('the file system is Single-AZ, 32 GiB, 8 MB/s, joined with a dynamic-reference password', () => {
    const template = Template.fromStack(fileSystem);
    template.resourceCountIs('AWS::FSx::FileSystem', 1);
    const [fs] = Object.values(template.findResources('AWS::FSx::FileSystem'));
    const win = fs.Properties.WindowsConfiguration;
    expect(fs.Properties.StorageCapacity).toBe(32);
    expect(win.DeploymentType).toBe('SINGLE_AZ_2');
    expect(win.ThroughputCapacity).toBe(8);
    expect(win.AutomaticBackupRetentionDays).toBe(7);
    expect(JSON.stringify(win.SelfManagedActiveDirectoryConfiguration.Password)).toContain('{{resolve:secretsmanager:');
    expect(win.SelfManagedActiveDirectoryConfiguration.DnsIps).toEqual(['10.0.1.10']);
  });

  test('exactly one AWS/FSx alarm is scoped to the file system, plus the canary alarm', () => {
    const template = Template.fromStack(fileSystem);
    const alarms = Object.values(template.findResources('AWS::CloudWatch::Alarm')).map(a => a.Properties);
    const fsxAlarms = alarms.filter(a => a.Namespace === 'AWS/FSx');
    expect(fsxAlarms).toHaveLength(1);
    expect(fsxAlarms[0].MetricName).toBe('FreeStorageCapacity');
    expect(fsxAlarms[0].Threshold).toBe(Math.round(32 * 1024 ** 3 * 0.2));
    expect(alarms.filter(a => a.MetricName === 'FileSystemMisconfigured')).toHaveLength(1);
    template.hasResourceProperties('AWS::Events::Rule', { ScheduleExpression: 'rate(1 minute)' });
  });

  test('the domain controller has a fixed private IP and promotes the forest from its user data', () => {
    const template = Template.fromStack(directory);
    template.hasResourceProperties('AWS::EC2::Instance', { PrivateIpAddress: '10.0.1.10' });
    const [instance] = Object.values(template.findResources('AWS::EC2::Instance'));
    const userData = JSON.stringify(instance.Properties.UserData);
    expect(userData).toContain('Install-ADDSForest');
    expect(userData).toContain('<persist>true</persist>');
    expect(userData).toContain('Write-SSMParameter');
    expect(userData).not.toMatch(/password\s*=\s*'[^$]/i);
  });

  test('the Lab site checks Basic Auth at the edge and denies everything without a password', () => {
    const template = Template.fromStack(lab);
    const [fn] = Object.values(template.findResources('AWS::CloudFront::Function'));
    expect(fn.Properties.FunctionCode).toContain(Buffer.from('presenter:s3cret').toString('base64'));
    expect(fn.Properties.FunctionCode).toContain('delete request.headers.authorization');
    template.hasResourceProperties('AWS::Lambda::Url', { AuthType: 'AWS_IAM' });
    const distribution = Object.values(template.findResources('AWS::CloudFront::Distribution'))[0].Properties.DistributionConfig;
    expect(distribution.CacheBehaviors.map((b: { PathPattern: string }) => b.PathPattern)).toEqual(['/admin/*']);

    const denyAll = Template.fromStack(synth('').lab);
    const [denyFn] = Object.values(denyAll.findResources('AWS::CloudFront::Function'));
    expect(denyFn.Properties.FunctionCode).toContain('var expected = "";');
  });

  test('the Agent Space stack exposes what deploy-all reads', () => {
    const outputs = Object.keys(Template.fromStack(agentSpace).toJSON().Outputs ?? {});
    expect(outputs).toEqual(expect.arrayContaining(['AgentSpaceId', 'WebhookUrl', 'WebhookSecretArn']));
  });
});
