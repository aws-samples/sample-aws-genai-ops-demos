#!/usr/bin/env npx ts-node
/**
 * Cleanup Script — Proactive Health Event Impact Analyzer
 *
 * Removes all resources created by the setup wizard:
 * 1. Destroys the CDK stack (Lambda, Step Functions, DynamoDB, SNS, EventBridge)
 * 2. Deletes the DevOps Agent Space (and all associations/webhooks)
 * 3. Removes IAM roles (DevOpsAgentRole-AgentSpace, DevOpsAgentRole-WebappAdmin)
 *
 * Usage: npx ts-node scripts/cleanup.ts
 */

import { execSync } from 'child_process';
import * as readline from 'readline';
import * as path from 'path';

// ─── Utilities ──────────────────────────────────────────────────────────────

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

function ask(question: string): Promise<string> {
  return new Promise((resolve) => {
    rl.question(question, (answer) => resolve(answer.trim()));
  });
}

async function askYesNo(question: string, defaultYes = false): Promise<boolean> {
  const hint = defaultYes ? '[Y/n]' : '[y/N]';
  const answer = await ask(`${question} ${hint}: `);
  if (answer === '') return defaultYes;
  return answer.toLowerCase().startsWith('y');
}

async function askChoice(question: string, options: string[]): Promise<number> {
  console.log(`\n${question}`);
  options.forEach((opt, i) => console.log(`  ${i + 1}. ${opt}`));
  const answer = await ask(`\nSelect (1-${options.length}): `);
  const idx = parseInt(answer, 10) - 1;
  if (idx < 0 || idx >= options.length) {
    console.log('Invalid selection, please try again.');
    return askChoice(question, options);
  }
  return idx;
}

function exec(command: string, silent = false): string {
  try {
    const result = execSync(command, { encoding: 'utf-8', stdio: 'pipe' });
    return result.trim();
  } catch (error: any) {
    if (!silent) {
      console.error(`  ⚠️  Command failed: ${command}`);
    }
    throw error;
  }
}

function execJson(command: string): any {
  const result = exec(command, true);
  return JSON.parse(result);
}

function banner(text: string): void {
  const line = '═'.repeat(60);
  console.log(`\n${line}`);
  console.log(`  ${text}`);
  console.log(`${line}\n`);
}

function success(text: string): void {
  console.log(`  ✅ ${text}`);
}

function info(text: string): void {
  console.log(`  ℹ️  ${text}`);
}

function warn(text: string): void {
  console.log(`  ⚠️  ${text}`);
}

function skipped(text: string): void {
  console.log(`  ⏭️  ${text}`);
}

// ─── Supported Regions ──────────────────────────────────────────────────────

const SUPPORTED_REGIONS = [
  'us-east-1',
  'us-west-2',
  'ap-southeast-2',
  'ap-northeast-1',
  'eu-central-1',
  'eu-west-1',
];

// ─── Atlassian Jira MCP constants ───────────────────────────────────────────

// Must match the constants used by setup-wizard.ts.
const ATLASSIAN_MCP_NAME = 'atlassian-jira';
const SSM_PARAM_JIRA_PROJECT_KEY = '/health-analyzer/jira/projectKey';
const SSM_PARAM_JIRA_ISSUE_TYPE = '/health-analyzer/jira/issueType';
const SSM_PARAM_JIRA_SITE_URL = '/health-analyzer/jira/siteUrl';
const JIRA_SSM_PARAMS = [
  SSM_PARAM_JIRA_PROJECT_KEY,
  SSM_PARAM_JIRA_ISSUE_TYPE,
  SSM_PARAM_JIRA_SITE_URL,
];

// SSM SecureString parameters for the Slack/MS Teams webhook secrets (created
// by the setup wizard). The DevOps Agent webhook secret is no longer among
// these — it now lives in a CDK-managed Secrets Manager secret (RemovalPolicy
// DESTROY), which `cdk destroy` on the Agent Space stack removes automatically.
const SECRET_SSM_PARAMS = [
  '/health-analyzer/production/slack-webhook-url',
  '/health-analyzer/production/msteams-webhook-url',
  '/health-analyzer/staging/slack-webhook-url',
  '/health-analyzer/staging/msteams-webhook-url',
];

/**
 * Resolves the Agent Space region, mirroring scripts/setup-wizard.ts's
 * resolveAgentRegion(): DEVOPS_AGENT_REGION overrides, otherwise same as the
 * deploy region. Destroying HealthEventAnalyzerAgentSpace-<region> requires
 * this to match whatever region it was actually deployed to, since `cdk
 * destroy` re-synthesizes the app to resolve stack names before deleting.
 */
function resolveAgentRegion(deployRegion: string): string {
  const override = process.env.DEVOPS_AGENT_REGION;
  if (override && override.trim()) return override.trim();
  return deployRegion;
}

// ─── Main ───────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  banner('Proactive Health Event Impact Analyzer — Cleanup');

  console.log('  ⚠️  This script will PERMANENTLY DELETE all resources created by the setup wizard.');
  console.log('  This includes: CDK stack, DevOps Agent Space, IAM roles, DynamoDB tables, etc.');
  console.log('');

  // Verify credentials
  let accountId = '';
  try {
    const identity = execJson('aws sts get-caller-identity --no-cli-pager');
    accountId = identity.Account;
    info(`Authenticated as: ${identity.Arn}`);
    info(`Account: ${accountId}`);
  } catch {
    console.error('❌ AWS credentials not configured or session expired. Run: aws sso login');
    rl.close();
    return;
  }

  // Select region
  const regionIdx = await askChoice('Which region do you want to clean up?', SUPPORTED_REGIONS);
  const region = SUPPORTED_REGIONS[regionIdx];
  const agentRegion = resolveAgentRegion(region);
  info(`Region: ${region}`);
  if (agentRegion !== region) {
    info(`Agent Space region: ${agentRegion} (from DEVOPS_AGENT_REGION)`);
  }

  const confirmAll = await askYesNo(
    `\n  Are you sure you want to delete ALL resources in ${region}${agentRegion !== region ? ` and ${agentRegion}` : ''} for account ${accountId}?`
  );
  if (!confirmAll) {
    console.log('\n  Cleanup cancelled.');
    rl.close();
    return;
  }

  // ─── Step 1: Destroy CDK Stacks ─────────────────────────────────────────
  // The DevOps Agent Space, its IAM roles, the AWS account association, the
  // operator app, and the eventChannel webhook (including its Secrets
  // Manager secret) are all now CDK-managed — destroying
  // HealthEventAnalyzerAgentSpace-<agentRegion> tears them all down, replacing
  // what used to be separate imperative "delete Agent Space" / "deregister
  // service" / "delete IAM roles" steps here.
  console.log('\n┌─ Step 1: CDK Stacks');
  console.log('└' + '─'.repeat(55));

  const stackName = `HealthEventAnalyzerStack-${region}`;
  const agentSpaceStackName = `HealthEventAnalyzerAgentSpace-${agentRegion}`;
  const cdkDir = path.resolve(__dirname, '../infrastructure/cdk');

  try {
    exec(`aws cloudformation describe-stacks --stack-name ${stackName} --region ${region} --no-cli-pager`, true);
    info(`Found stack: ${stackName}`);

    const destroyStack = await askYesNo(`  Destroy CDK stack ${stackName}?`);
    if (destroyStack) {
      info('Destroying main CDK stack (this may take a few minutes)...');
      try {
        execSync(
          `npx cdk destroy ${stackName} -c devOpsAgentRegion=${agentRegion} --force`,
          { cwd: cdkDir, encoding: 'utf-8', stdio: 'inherit', env: { ...process.env, AWS_REGION: region, AWS_DEFAULT_REGION: region } }
        );
        success(`Stack destroyed: ${stackName}`);
      } catch {
        warn('CDK destroy had issues. You may need to delete the stack manually from CloudFormation console.');
      }
    } else {
      skipped('Main CDK stack preserved');
    }
  } catch {
    info(`Stack ${stackName} not found — nothing to destroy`);
  }

  try {
    exec(`aws cloudformation describe-stacks --stack-name ${agentSpaceStackName} --region ${agentRegion} --no-cli-pager`, true);
    info(`Found stack: ${agentSpaceStackName}`);

    const destroySpaceStack = await askYesNo(
      `  ⚠️  Destroy DevOps Agent Space stack ${agentSpaceStackName}? This deletes the Agent Space and ALL its associations, webhooks, and investigations.`
    );
    if (destroySpaceStack) {
      info('Destroying DevOps Agent Space stack (this may take a few minutes)...');
      try {
        // -c devOpsAgentRegion must match the value used at deploy time so CDK
        // resolves the same stack ID (HealthEventAnalyzerAgentSpace-<agentRegion>).
        execSync(
          `npx cdk destroy ${agentSpaceStackName} -c devOpsAgentRegion=${agentRegion} --force`,
          { cwd: cdkDir, encoding: 'utf-8', stdio: 'inherit', env: { ...process.env, AWS_REGION: agentRegion, AWS_DEFAULT_REGION: agentRegion } }
        );
        success(`Stack destroyed: ${agentSpaceStackName}`);
      } catch {
        warn('CDK destroy had issues. You may need to delete the stack manually from CloudFormation console.');
      }
    } else {
      skipped('DevOps Agent Space stack preserved');
    }
  } catch {
    info(`Stack ${agentSpaceStackName} not found — nothing to destroy`);
  }

  // ─── Step 2: Jira routing config (SSM Parameter Store) ──────────────────
  console.log('\n┌─ Step 2: Jira routing config (SSM Parameter Store)');
  console.log('└' + '─'.repeat(55));

  await deleteJiraSsmParams(region);

  // ─── Step 3: Slack/MS Teams Secret SSM Parameters ───────────────────────
  console.log('\n┌─ Step 3: Slack/MS Teams Secret SSM Parameters');
  console.log('└' + '─'.repeat(55));

  await deleteSsmParams(region, SECRET_SSM_PARAMS, 'secret');

  // ─── Done ───────────────────────────────────────────────────────────────
  banner('Cleanup Complete');

  console.log('  All selected resources have been removed.');
  console.log('  If any steps failed, check the AWS Console for remaining resources.');
  console.log('');

  rl.close();
}

// ─── Helper Functions ───────────────────────────────────────────────────────

async function deleteJiraSsmParams(region: string): Promise<void> {
  await deleteSsmParams(region, JIRA_SSM_PARAMS, 'Jira');
}

async function deleteSsmParams(region: string, params: string[], label: string): Promise<void> {
  let foundAny = false;
  for (const name of params) {
    try {
      exec(
        `aws ssm get-parameter --name "${name}" --region ${region} --no-cli-pager`,
        true
      );
    } catch {
      continue;
    }
    foundAny = true;
    try {
      exec(
        `aws ssm delete-parameter --name "${name}" --region ${region} --no-cli-pager`,
        true
      );
      success(`Deleted SSM parameter: ${name}`);
    } catch {
      warn(`Failed to delete SSM parameter ${name} — delete it manually if needed.`);
    }
  }
  if (!foundAny) {
    info(`No ${label} SSM parameters found in this region — nothing to delete.`);
  }
}

// removeJiraSsmReadGrant() and deleteIamRole() (for DevOpsAgentRole-AgentSpace
// / DevOpsAgentRole-WebappAdmin) used to live here. Both are gone now: the
// IAM roles are CDK-managed under different, project-prefixed names
// (health-event-analyzer-AgentSpaceRole / -OperatorRole — see
// infrastructure/cdk/lib/constructs/devops-agent-space.ts) and are deleted
// automatically when HealthEventAnalyzerAgentSpace-<region> is destroyed above.

// ─── Entry Point ────────────────────────────────────────────────────────────

main().catch((error) => {
  console.error('\n❌ Cleanup failed:', error.message);
  rl.close();
});
