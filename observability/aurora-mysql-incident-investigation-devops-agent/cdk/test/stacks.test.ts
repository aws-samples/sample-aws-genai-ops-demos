/**
 * The three stacks synthesize and keep the promises the README makes: one tracked stack,
 * region-suffixed ids, a db.t4g.medium writer with Performance Insights and no public path,
 * the business tags on the cluster only, a load function that is off at deploy (reserved
 * concurrency 0), one DatabaseConnections alarm wired to the trigger chain, the agent's
 * Config read permission, and a Lab site that denies everything without a password.
 */
import * as cdk from 'aws-cdk-lib';
import * as fs from 'fs';
import * as path from 'path';
import { Template } from 'aws-cdk-lib/assertions';
import { AgentSpaceStack } from '../lib/agent-space-stack';
import { BUSINESS_TAGS, CONNECTIONS_THRESHOLD } from '../lib/constants';
import { DatabaseStack } from '../lib/database-stack';
import { LAB_REQUEST_HEADER, LabStack } from '../lib/lab-stack';

const TRACKING = '(uksb-do9bhieqqh)(tag:aurora-mysql-incident-investigation-devops-agent,observability)';

function synth(labPassword: string, region = 'us-east-1') {
  const app = new cdk.App();
  const env = { region };
  const projectName = 'aurora-bizctx';
  const agentSpace = new AgentSpaceStack(app, `AuroraBizCtxAgentSpace-${region}`, { env, projectName, description: 'Agent Space' });
  const database = new DatabaseStack(app, `AuroraBizCtxDatabase-${region}`, {
    env, projectName,
    webhookUrl: 'https://example.com/webhook', webhookSecretArn: 'arn:aws:secretsmanager:us-east-1:123456789012:secret:test-AbCdEf',  // pragma: allowlist secret
    webhookSecretRegion: region, description: `Aurora MySQL incident investigation ${TRACKING}`,
  });
  const lab = new LabStack(app, `AuroraBizCtxLab-${region}`, {
    env, projectName, database, devOpsAgentRegion: region, devOpsAgentSpaceId: 'space-1', labUser: 'presenter', labPassword, description: 'Lab',
  });
  return { app, agentSpace, database, lab };
}

describe('Aurora MySQL business-context stacks', () => {
  const { app, agentSpace, database, lab } = synth('s3cret');
  const assembly = app.synth();

  test('three region-suffixed stacks, tracking on the database stack only', () => {
    const names = assembly.stacks.map(s => s.stackName);
    expect(names).toEqual(expect.arrayContaining(['AuroraBizCtxAgentSpace-us-east-1', 'AuroraBizCtxDatabase-us-east-1', 'AuroraBizCtxLab-us-east-1']));
    const tracked = assembly.stacks.filter(s => JSON.stringify(s.template.Description ?? '').includes('uksb-do9bhieqqh'));
    expect(tracked.map(s => s.stackName)).toEqual(['AuroraBizCtxDatabase-us-east-1']);
    expect(tracked[0].template.Description).toContain(TRACKING);
  });

  test('one private db.t4g.medium writer with Performance Insights, nothing left on destroy', () => {
    const template = Template.fromStack(database);
    template.resourceCountIs('AWS::RDS::DBCluster', 1);
    template.resourceCountIs('AWS::RDS::DBInstance', 1);
    template.hasResourceProperties('AWS::RDS::DBInstance', {
      DBInstanceClass: 'db.t4g.medium', EnablePerformanceInsights: true, PubliclyAccessible: false, DBInstanceIdentifier: 'aurora-bizctx-writer',
    });
    const [cluster] = Object.values(template.findResources('AWS::RDS::DBCluster'));
    expect(cluster.Properties.Engine).toBe('aurora-mysql');
    expect(cluster.Properties.StorageEncrypted).toBe(true);
    expect(cluster.Properties.DeletionProtection).toBe(false);
    expect(cluster.DeletionPolicy).toBe('Delete');
    expect(cluster.Properties.EnableCloudwatchLogsExports).toBeUndefined();
    template.resourceCountIs('AWS::EC2::NatGateway', 0);
    template.resourceCountIs('AWS::EC2::InternetGateway', 0);
    for (const secret of Object.values(template.findResources('AWS::SecretsManager::Secret'))) {
      expect(secret.DeletionPolicy).toBe('Delete');
    }
  });

  test('the business tags sit on the cluster and nowhere else', () => {
    const template = Template.fromStack(database);
    const [cluster] = Object.values(template.findResources('AWS::RDS::DBCluster'));
    const clusterTags = Object.fromEntries((cluster.Properties.Tags as { Key: string; Value: string }[]).map(t => [t.Key, t.Value]));
    expect(clusterTags).toEqual(expect.objectContaining(BUSINESS_TAGS));
    expect(clusterTags.Compliance).toBeUndefined();
    const elsewhere = JSON.stringify(Object.entries(template.toJSON().Resources)
      .filter(([, r]) => (r as { Type: string }).Type !== 'AWS::RDS::DBCluster'));
    for (const key of Object.keys(BUSINESS_TAGS)) {
      expect(elsewhere).not.toContain(`"Key":"${key}"`);
    }
  });

  test('the load function is off at deploy, in the VPC, and can reach the API it needs', () => {
    const template = Template.fromStack(database);
    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'aurora-bizctx-connection-load', ReservedConcurrentExecutions: 0, Timeout: 900,
    });
    const [fn] = Object.values(template.findResources('AWS::Lambda::Function', { Properties: { FunctionName: 'aurora-bizctx-connection-load' } }));
    expect(fn.Properties.VpcConfig).toBeDefined();
    template.hasResourceProperties('AWS::Lambda::EventInvokeConfig', { MaximumRetryAttempts: 0, MaximumEventAgeInSeconds: 120 });
    const services = Object.values(template.findResources('AWS::EC2::VPCEndpoint')).map(e => JSON.stringify(e.Properties.ServiceName));
    expect(services.some(s => s.includes('secretsmanager'))).toBe(true);
    expect(services.some(s => s.includes('lambda'))).toBe(true);
    expect(services).toHaveLength(2);
  });

  test('exactly one DatabaseConnections alarm on the writer, notifying the trigger chain', () => {
    const template = Template.fromStack(database);
    const alarms = Object.values(template.findResources('AWS::CloudWatch::Alarm')).map(a => a.Properties);
    expect(alarms).toHaveLength(1);
    expect(alarms[0]).toEqual(expect.objectContaining({
      Namespace: 'AWS/RDS', MetricName: 'DatabaseConnections', Statistic: 'Maximum', Period: 60,
      Threshold: CONNECTIONS_THRESHOLD, EvaluationPeriods: 2, DatapointsToAlarm: 2, ComparisonOperator: 'GreaterThanThreshold',
      Dimensions: [{ Name: 'DBInstanceIdentifier', Value: 'aurora-bizctx-writer' }],
    }));
    expect(alarms[0].AlarmActions).toHaveLength(1);
    expect(alarms[0].AlarmDescription).not.toMatch(/lab|demo|inject|test/i);
    template.resourceCountIs('AWS::SNS::Subscription', 2);   // the alarms topic and the trigger's own topic
  });

  test('the Agent Space role can read AWS Config history; outputs are what deploy-all reads', () => {
    const template = Template.fromStack(agentSpace);
    const policies = JSON.stringify(template.findResources('AWS::IAM::Policy'));
    expect(policies).toContain('config:GetResourceConfigHistory');
    const statements = Object.values(template.findResources('AWS::IAM::Policy'))
      .flatMap(p => p.Properties.PolicyDocument.Statement as { Sid?: string; Action: string | string[]; Resource: unknown }[]);
    const load = statements.find(s => s.Sid === 'ReadTheLoadFunction')!;
    expect(([] as string[]).concat(load.Action).sort()).toEqual(['lambda:GetFunction', 'lambda:GetFunctionConcurrency']);
    const loadResources = JSON.stringify(load.Resource);
    expect(loadResources).toContain(':function:aurora-bizctx-connection-load');
    expect(loadResources).toContain(':lambda:us-east-1:');
    // The region comes from the stack, never a literal: another region gives another ARN.
    const elsewhere = JSON.stringify(Template.fromStack(synth('s3cret', 'eu-west-1').agentSpace).findResources('AWS::IAM::Policy'));
    expect(elsewhere).toContain(':lambda:eu-west-1:');
    expect(elsewhere).toContain(':rds:eu-west-1:');
    expect(elsewhere).not.toContain('us-east-1');
    const logs = statements.find(s => s.Sid === 'ReadTheWriterLogs')!;
    expect(logs.Action).toBe('rds:DownloadDBLogFilePortion');
    expect(JSON.stringify(logs.Resource)).toContain(':db:aurora-bizctx-writer');
    // The Lab engine's durable executions stay unreadable to the agent.
    expect(policies).not.toContain('lambda:GetDurableExecution');
    for (const s of statements.filter(s => JSON.stringify(s.Resource) === '"*"')) {
      expect(([] as string[]).concat(s.Action).some(a => /^(lambda|rds):/.test(a))).toBe(false);
    }
    const outputs = Object.keys(template.toJSON().Outputs ?? {});
    expect(outputs).toEqual(expect.arrayContaining(['AgentSpaceId', 'WebhookUrl', 'WebhookSecretArn']));
  });

  test('the Lab role writes only to the one cluster and the one load function', () => {
    // The API function behind CloudFront shares this role with the engine (LabEngine design): pin its scope.
    const template = Template.fromStack(lab);
    const statements = Object.values(template.findResources('AWS::IAM::Policy'))
      .flatMap(p => p.Properties.PolicyDocument.Statement as { Sid?: string; Action: string | string[]; Resource: unknown }[]);
    const actions = (s: { Action: string | string[] }) => ([] as string[]).concat(s.Action);
    const resourceText = (s: { Resource: unknown }) => JSON.stringify(s.Resource);
    const writes = ['rds:AddTagsToResource', 'rds:RemoveTagsFromResource', 'lambda:PutFunctionConcurrency'];
    for (const action of writes) {
      const granting = statements.filter(s => actions(s).includes(action));
      expect(granting).toHaveLength(1);
      expect(resourceText(granting[0])).not.toBe('"*"');
    }
    const tagStatement = statements.find(s => s.Sid === 'ChangeTheClusterTags')!;
    expect(resourceText(tagStatement)).toContain(':cluster:aurora-bizctx-cluster');
    const loadStatement = statements.find(s => s.Sid === 'SwitchTheLoadFunction')!;
    expect(resourceText(loadStatement)).toContain(':function:aurora-bizctx-connection-load');
    expect(actions(loadStatement)).toEqual(expect.arrayContaining(['lambda:InvokeFunction', 'lambda:PutFunctionConcurrency']));
    // No rds:* or lambda:* write on '*' anywhere in the Lab.
    for (const s of statements.filter(s => resourceText(s) === '"*"')) {
      for (const a of actions(s)) {
        expect(a).toMatch(/^(rds:Describe\w+|cloudwatch:(GetMetricData|DescribeAlarms)|aidevops:(Get|List)\w+)$/);
      }
    }
  });

  test('the Lab restores the same tags CDK created and checks Basic Auth at the edge', () => {
    const template = Template.fromStack(lab);
    const engine = Object.values(template.findResources('AWS::Lambda::Function'))
      .find(f => JSON.stringify(f.Properties.FunctionName ?? '').includes('lab-engine'))!;
    expect(JSON.parse(engine.Properties.Environment.Variables.BASELINE_TAGS)).toEqual(BUSINESS_TAGS);
    expect(engine.Properties.Timeout).toBe(360);
    const [fn] = Object.values(template.findResources('AWS::CloudFront::Function'));
    expect(fn.Properties.FunctionCode).toContain(Buffer.from('presenter:s3cret').toString('base64'));
    expect(fn.Properties.FunctionCode).toContain('delete request.headers.authorization');
    // Non-GET /admin/* calls need the header only the Lab's own fetch sets, and the UI sets the same one.
    expect(fn.Properties.FunctionCode).toContain(`!request.headers['${LAB_REQUEST_HEADER}']`);
    expect(fn.Properties.FunctionCode).toContain("statusCode: 403");
    const ui = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'src', 'lab', 'api.ts'), 'utf-8');
    expect(ui).toContain(`const LAB_REQUEST_HEADER = '${LAB_REQUEST_HEADER}'`);
    expect(ui).toContain('[LAB_REQUEST_HEADER]: ');
    template.hasResourceProperties('AWS::Lambda::Url', { AuthType: 'AWS_IAM' });
    const denyAll = Template.fromStack(synth('').lab);
    const [denyFn] = Object.values(denyAll.findResources('AWS::CloudFront::Function'));
    expect(denyFn.Properties.FunctionCode).toContain('var expected = "";');
  });
});
