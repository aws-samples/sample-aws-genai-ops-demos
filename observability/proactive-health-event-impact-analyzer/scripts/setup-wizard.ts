#!/usr/bin/env npx ts-node
/**
 * Setup Wizard — Proactive Health Event Impact Analyzer
 *
 * Interactive guided deployment that:
 * 1. Checks prerequisites (AWS CLI, CDK, credentials)
 * 2. Creates IAM roles for DevOps Agent (if needed)
 * 3. Creates or selects a DevOps Agent Space
 * 4. Associates the AWS account for topology discovery
 * 5. Creates or selects a webhook (eventChannel)
 * 6. Enables the operator app
 * 7. Deploys the CDK stack with all parameters
 *
 * Usage: npx ts-node scripts/setup-wizard.ts
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

async function askYesNo(question: string, defaultYes = true): Promise<boolean> {
  const hint = defaultYes ? '[Y/n]' : '[y/N]';
  const answer = await ask(`${question} ${hint}: `);
  if (answer === '') return defaultYes;
  return answer.toLowerCase().startsWith('y');
}

async function askChoice(question: string, options: string[]): Promise<number> {
  console.log(`\n${question}`);
  options.forEach((opt, i) => console.log(`  ${i + 1}. ${opt}`));
  const promptSuffix = options.length === 1 ? ' [1]' : '';
  const answer = await ask(`\nSelect (1-${options.length})${promptSuffix}: `);

  // When there's only one option, treat empty input as selecting it.
  if (answer === '' && options.length === 1) {
    return 0;
  }

  const parsed = parseInt(answer, 10);
  const idx = parsed - 1;
  if (!Number.isInteger(parsed) || idx < 0 || idx >= options.length) {
    console.log(`Invalid selection. Please enter a number between 1 and ${options.length}.`);
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
      console.error(`\n❌ Command failed: ${command}`);
      console.error(error.stderr || error.message);
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

function step(num: number, text: string): void {
  console.log(`\n┌─ Step ${num}: ${text}`);
  console.log('└' + '─'.repeat(55));
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

// ─── Step Result Tracking ────────────────────────────────────────────────────

type StepStatus = 'succeeded' | 'failed' | 'skipped';

interface StepResult {
  name: string;
  status: StepStatus;
  error?: string;
}

const stepResults: StepResult[] = [];

function recordStep(name: string, status: StepStatus, error?: string): void {
  stepResults.push({ name, status, error });
}

/**
 * Prompt the user with retry/skip/abort options when a step fails.
 * Returns 'retry' | 'skip' | 'abort'.
 */
async function askRetrySkipAbort(stepName: string, errorMsg: string): Promise<'retry' | 'skip' | 'abort'> {
  console.error(`\n  ❌ Step "${stepName}" failed: ${errorMsg}`);
  const choice = await askChoice('How would you like to proceed?', [
    'Retry this step',
    'Skip this step and continue',
    'Abort the wizard',
  ]);
  if (choice === 0) return 'retry';
  if (choice === 1) return 'skip';
  return 'abort';
}

/**
 * Run a wizard step with retry/skip/abort error handling.
 * Never throws — always returns gracefully.
 */
async function runStep(
  stepName: string,
  fn: () => Promise<void>
): Promise<boolean> {
  while (true) {
    try {
      await fn();
      recordStep(stepName, 'succeeded');
      return true;
    } catch (error: any) {
      const errorMsg = error?.message || String(error);
      const action = await askRetrySkipAbort(stepName, errorMsg);
      if (action === 'retry') continue;
      if (action === 'skip') {
        recordStep(stepName, 'skipped', errorMsg);
        skipped(`Skipped: ${stepName}`);
        return false;
      }
      // abort
      recordStep(stepName, 'failed', errorMsg);
      return false;
    }
  }
}

/**
 * Display a completion summary showing which steps succeeded, which failed,
 * and which were skipped. Provides actionable guidance for resolving failures.
 */
function displayCompletionSummary(): void {
  const line = '─'.repeat(60);
  console.log(`\n${line}`);
  console.log('  Completion Summary');
  console.log(`${line}`);

  const succeeded = stepResults.filter(r => r.status === 'succeeded');
  const failed = stepResults.filter(r => r.status === 'failed');
  const skippedSteps = stepResults.filter(r => r.status === 'skipped');

  if (succeeded.length > 0) {
    console.log(`\n  ✅ Succeeded (${succeeded.length}):`);
    for (const s of succeeded) {
      console.log(`     • ${s.name}`);
    }
  }

  if (skippedSteps.length > 0) {
    console.log(`\n  ⏭️  Skipped (${skippedSteps.length}):`);
    for (const s of skippedSteps) {
      console.log(`     • ${s.name}${s.error ? ` — ${s.error}` : ''}`);
    }
  }

  if (failed.length > 0) {
    console.log(`\n  ❌ Failed (${failed.length}):`);
    for (const s of failed) {
      console.log(`     • ${s.name}${s.error ? ` — ${s.error}` : ''}`);
    }
    console.log('\n  Guidance:');
    console.log('    • Re-run the wizard to retry failed steps');
    console.log('    • Check AWS credentials and permissions');
    console.log('    • Review error messages above for specific remediation');
  }

  if (failed.length === 0 && skippedSteps.length === 0) {
    console.log('\n  All steps completed successfully! 🎉');
  }

  console.log(`\n${line}\n`);
}

// ─── State ──────────────────────────────────────────────────────────────────

interface SetupState {
  region: string;
  accountId: string;
  agentSpaceId: string;
  agentSpaceName: string;
  associationId: string;
  webhookUrl: string;
  webhookSecret: string;
  operatorAppEnabled: boolean;
  notificationEmail: string;
  slackWebhookUrl: string;
  msTeamsWebhookUrl: string;
  jiraEnabled: boolean;
  jiraSiteUrl: string;
  jiraProjectKey: string;
  jiraIssueType: string;
  jiraMcpServiceId: string;
  jiraMcpAssociationId: string;
}

// ─── Atlassian Jira MCP constants ───────────────────────────────────────────

// The Atlassian Rovo MCP Server endpoint. The legacy /v1/sse path will sunset
// on 30 June 2026; the /v1/mcp/authv2 path is the supported successor.
// Source: https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/
const ATLASSIAN_MCP_ENDPOINT = 'https://mcp.atlassian.com/v1/mcp/authv2';

// Display name for the registered MCP server. Must match the AWS DevOps Agent
// pattern ^[a-zA-Z0-9_-]+$ and be ≤ 64 chars.
const ATLASSIAN_MCP_NAME = 'atlassian-jira';

// Tools to allow-list per the user's preference: create + comment only.
// Reading and search remain available so the agent can de-dup before creating.
//
// These are the names documented at
// https://support.atlassian.com/atlassian-rovo-mcp-server/docs/supported-tools/
// but they're used only as a fallback. The wizard probes the live server via
// MCP `tools/list` at runtime and selects whatever matches our intent
// (regardless of any prefix Atlassian may use on the wire).
const ATLASSIAN_MCP_DEFAULT_TOOLS = [
  // Discovery (one-time bootstrap and de-dup)
  'getAccessibleAtlassianResources',
  'atlassianUserInfo',
  'getVisibleJiraProjects',
  'getJiraProjectIssueTypesMetadata',
  'lookupJiraAccountId',
  // Read (de-dup before create)
  'getJiraIssue',
  'searchJiraIssuesUsingJql',
  // Write — create + comment only (no edit/transition by design)
  'createJiraIssue',
  'addCommentToJiraIssue',
];

// Patterns matched against tool names returned by the Atlassian server's
// `tools/list` MCP call. Designed to be tolerant of any prefix scheme the
// server may use (e.g. bare `getJiraIssue`, `Atlassian__getJiraIssue`,
// `read_jira.getJiraIssue`). Order is preserved so the resulting list is
// stable across runs.
const ATLASSIAN_TOOL_PATTERNS: RegExp[] = [
  /^(.*[_:.-])?getAccessibleAtlassianResources$/i,
  /^(.*[_:.-])?atlassianUserInfo$/i,
  /^(.*[_:.-])?getVisibleJiraProjects$/i,
  /^(.*[_:.-])?getJiraProjectIssueTypesMetadata$/i,
  /^(.*[_:.-])?lookupJiraAccountId$/i,
  /^(.*[_:.-])?getJiraIssue$/i,
  /^(.*[_:.-])?searchJiraIssuesUsingJql$/i,
  /^(.*[_:.-])?createJiraIssue$/i,
  /^(.*[_:.-])?addCommentToJiraIssue$/i,
];

// SSM Parameter Store paths the agent reads at runtime to know which Jira
// project to file tickets in. Non-secret — the API token lives only inside
// the AWS DevOps Agent registration.
const SSM_PARAM_JIRA_PROJECT_KEY = '/health-analyzer/jira/projectKey';
const SSM_PARAM_JIRA_ISSUE_TYPE = '/health-analyzer/jira/issueType';
const SSM_PARAM_JIRA_SITE_URL = '/health-analyzer/jira/siteUrl';

// ─── Supported Regions ──────────────────────────────────────────────────────

const SUPPORTED_REGIONS = [
  'us-east-1',
  'us-west-2',
  'ap-southeast-2',
  'ap-northeast-1',
  'eu-central-1',
  'eu-west-1',
];

// ─── AWS CLI version requirements ───────────────────────────────────────────

// `aws devops-agent` was introduced as a public CLI subcommand with the AWS
// DevOps Agent GA release in AWS CLI v2.34.20. Earlier versions parse-fail
// with: "argument command: Found invalid choice 'devops-agent'".
// See: https://github.com/aws/aws-cli/blob/v2/CHANGELOG.rst
const MIN_AWS_CLI_VERSION = '2.34.20';

function parseVersion(v: string): number[] {
  return v.split('.').map((n) => parseInt(n, 10) || 0);
}

function compareVersions(a: string, b: string): number {
  const pa = parseVersion(a);
  const pb = parseVersion(b);
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i++) {
    const x = pa[i] || 0;
    const y = pb[i] || 0;
    if (x !== y) return x - y;
  }
  return 0;
}

function getAwsCliVersion(): string | null {
  try {
    // `aws --version` prints to stderr in some installs; capture both.
    const output = execSync('aws --version 2>&1', { encoding: 'utf-8' }).trim();
    const match = output.match(/aws-cli\/(\d+\.\d+\.\d+)/);
    return match ? match[1] : null;
  } catch {
    return null;
  }
}

// ─── CLI args ───────────────────────────────────────────────────────────────

interface CliOptions {
  jiraOnly: boolean;
  region?: string;
  agentSpaceId?: string;
  jiraTools?: string[];
  help: boolean;
}

function parseArgs(argv: string[]): CliOptions {
  const opts: CliOptions = { jiraOnly: false, help: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--jira-only') opts.jiraOnly = true;
    else if (a === '--region') opts.region = argv[++i];
    else if (a === '--agent-space-id') opts.agentSpaceId = argv[++i];
    else if (a === '--jira-tools') {
      opts.jiraTools = (argv[++i] || '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
    } else if (a === '--help' || a === '-h') opts.help = true;
    else if (a.startsWith('--')) {
      console.error(`Unknown option: ${a}`);
      opts.help = true;
    }
  }
  return opts;
}

function printUsage(): void {
  console.log('Usage: npx ts-node scripts/setup-wizard.ts [options]');
  console.log('');
  console.log('Options:');
  console.log('  --jira-only             Run only the Atlassian Jira MCP setup against an');
  console.log('                          existing deployment. Skips prerequisites for IAM,');
  console.log('                          webhook, operator app, notifications, and CDK deploy.');
  console.log('  --region <name>         Pre-fill the region (skips the region prompt).');
  console.log('  --agent-space-id <id>   Pre-select the Agent Space (skips the picker).');
  console.log('  --jira-tools <list>     Comma-separated tool names to allow-list, overriding');
  console.log('                          the wizard\'s default list. Useful when AWS rejects the');
  console.log('                          defaults — discover the right names in the AWS DevOps');
  console.log('                          Agent console under Agent Space → Capabilities → MCP');
  console.log('                          Servers → Add. Pass an empty value (--jira-tools "")');
  console.log('                          to attempt allow-all.');
  console.log('  --help, -h              Show this help.');
}

// ─── Main ───────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    printUsage();
    rl.close();
    return;
  }

  if (args.jiraOnly) {
    await runJiraOnly(args);
    return;
  }

  banner('Proactive Health Event Impact Analyzer — Setup Wizard');

  const state: SetupState = {
    region: '',
    accountId: '',
    agentSpaceId: '',
    agentSpaceName: '',
    associationId: '',
    webhookUrl: '',
    webhookSecret: '',
    operatorAppEnabled: false,
    notificationEmail: '',
    slackWebhookUrl: '',
    msTeamsWebhookUrl: '',
    jiraEnabled: false,
    jiraSiteUrl: '',
    jiraProjectKey: '',
    jiraIssueType: 'Task',
    jiraMcpServiceId: '',
    jiraMcpAssociationId: '',
  };

  // ─── Step 0: Select Region (always first) ───────────────────────────────
  step(0, 'Select Target AWS Region');

  info('DevOps Agent is available in these regions:');
  const regionIdx = await askChoice('Which region do you want to deploy in?', SUPPORTED_REGIONS);
  state.region = SUPPORTED_REGIONS[regionIdx];
  success(`Region: ${state.region}`);
  recordStep('Select Region', 'succeeded');

  // ─── Step 1: Prerequisites ──────────────────────────────────────────────
  step(1, 'Checking prerequisites');

  let aborted = false;
  const prerequisitesOk = await runStep('Check prerequisites', async () => {
    try {
      exec('aws --version', true);
      success('AWS CLI installed');
    } catch {
      throw new Error('AWS CLI not found. Install it: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html');
    }

    const cliVersion = getAwsCliVersion();
    if (!cliVersion) {
      warn(`Could not parse AWS CLI version output. Continuing, but ${MIN_AWS_CLI_VERSION}+ is required for 'aws devops-agent'.`);
    } else if (compareVersions(cliVersion, MIN_AWS_CLI_VERSION) < 0) {
      throw new Error(
        `AWS CLI ${cliVersion} is too old. The 'aws devops-agent' command requires ${MIN_AWS_CLI_VERSION} or newer. ` +
        'Upgrade: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html'
      );
    } else {
      success(`AWS CLI ${cliVersion} (>= ${MIN_AWS_CLI_VERSION} required for devops-agent)`);
    }

    try {
      exec('npx cdk --version', true);
      success('AWS CDK available');
    } catch {
      throw new Error('AWS CDK not found. Install it: npm install -g aws-cdk');
    }

    try {
      const identity = execJson('aws sts get-caller-identity --no-cli-pager');
      state.accountId = identity.Account;
      success(`Authenticated as: ${identity.Arn}`);
      info(`Account: ${state.accountId}`);
    } catch {
      throw new Error('AWS credentials not configured or session expired. Run: aws sso login');
    }
  });

  if (!prerequisitesOk) {
    aborted = true;
    displayCompletionSummary();
    rl.close();
    return;
  }

  // ─── Step 2: DevOps Agent Space ─────────────────────────────────────────
  step(2, 'DevOps Agent Space');

  const spaceOk = await runStep('DevOps Agent Space', async () => {
    const existingSpaces = execJson(
      `aws devops-agent list-agent-spaces --region ${state.region} --no-cli-pager`
    );

    if (existingSpaces.agentSpaces && existingSpaces.agentSpaces.length > 0) {
      const useExisting = await askYesNo(
        `Found ${existingSpaces.agentSpaces.length} existing Agent Space(s). Use one of them?`
      );

      if (useExisting) {
        const spaceOptions = existingSpaces.agentSpaces.map(
          (s: any) => `${s.name} (${s.agentSpaceId})`
        );
        const spaceIdx = await askChoice('Select an Agent Space:', spaceOptions);
        state.agentSpaceId = existingSpaces.agentSpaces[spaceIdx].agentSpaceId;
        state.agentSpaceName = existingSpaces.agentSpaces[spaceIdx].name;
        success(`Using existing space: ${state.agentSpaceName}`);
      } else {
        await createAgentSpace(state);
      }
    } else {
      info('No existing Agent Spaces found. Creating a new one...');
      await createAgentSpace(state);
    }
  });

  if (!spaceOk && stepResults[stepResults.length - 1]?.status === 'failed') {
    aborted = true;
    displayCompletionSummary();
    rl.close();
    return;
  }

  // ─── Step 3: IAM Roles ──────────────────────────────────────────────────
  step(3, 'IAM Roles for DevOps Agent');

  const iamOk = await runStep('IAM Roles', async () => {
    await ensureIamRoles(state);
  });
  if (!iamOk && stepResults[stepResults.length - 1]?.status === 'failed') {
    aborted = true;
    displayCompletionSummary();
    rl.close();
    return;
  }

  // ─── Step 4: AWS Account Association ────────────────────────────────────
  step(4, 'AWS Account Association (topology discovery)');

  const assocOk = await runStep('Account Association', async () => {
    await ensureAccountAssociation(state);
  });
  if (!assocOk && stepResults[stepResults.length - 1]?.status === 'failed') {
    aborted = true;
    displayCompletionSummary();
    rl.close();
    return;
  }

  // ─── Step 5: Webhook ────────────────────────────────────────────────────
  step(5, 'Webhook Configuration');

  const webhookOk = await runStep('Webhook Configuration', async () => {
    await ensureWebhook(state);
  });
  if (!webhookOk && stepResults[stepResults.length - 1]?.status === 'failed') {
    aborted = true;
    displayCompletionSummary();
    rl.close();
    return;
  }

  // ─── Step 6: Operator App ───────────────────────────────────────────────
  step(6, 'Operator App');

  await runStep('Operator App', async () => {
    await ensureOperatorApp(state);
  });

  // ─── Step 7: Atlassian Jira (optional) ──────────────────────────────────
  step(7, 'Atlassian Jira integration (optional)');

  await runStep('Jira Integration', async () => {
    await ensureJiraMcp(state, args.jiraTools);
  });

  // ─── Step 8: Notification Channels (optional) ───────────────────────────
  step(8, 'Notification Channels (optional)');

  const wantEmail = await askYesNo('Configure email notifications?', false);
  if (wantEmail) {
    state.notificationEmail = await ask('  Email address: ');
  }

  const wantSlack = await askYesNo('Configure Slack notifications?', false);
  if (wantSlack) {
    state.slackWebhookUrl = await ask('  Slack webhook URL: ');
  }

  const wantTeams = await askYesNo('Configure Microsoft Teams notifications?', false);
  if (wantTeams) {
    state.msTeamsWebhookUrl = await ask('  MS Teams webhook URL: ');
  }
  recordStep('Notification Channels', 'succeeded');

  // ─── Step 9: CDK Bootstrap & Deploy ─────────────────────────────────────
  step(9, 'CDK Deployment');

  console.log('\n  Configuration summary:');
  console.log(`    Region:          ${state.region}`);
  console.log(`    Account:         ${state.accountId}`);
  console.log(`    Agent Space:     ${state.agentSpaceName} (${state.agentSpaceId})`);
  console.log(`    Webhook URL:     ${state.webhookUrl ? state.webhookUrl.substring(0, 60) + '...' : '(not set)'}`);
  console.log(`    Email:           ${state.notificationEmail || '(none)'}`);
  console.log(`    Slack:           ${state.slackWebhookUrl ? 'configured' : '(none)'}`);
  console.log(`    MS Teams:        ${state.msTeamsWebhookUrl ? 'configured' : '(none)'}`);
  console.log(
    `    Jira:            ${
      state.jiraEnabled
        ? `${state.jiraSiteUrl} → ${state.jiraProjectKey} (${state.jiraIssueType})`
        : '(none)'
    }`
  );

  const proceed = await askYesNo('\n  Proceed with deployment?');
  if (!proceed) {
    console.log('\n  Deployment cancelled. Your DevOps Agent setup is preserved.');
    recordStep('CDK Deployment', 'skipped', 'User cancelled');
    displayCompletionSummary();
    rl.close();
    return;
  }

  await runStep('CDK Deployment', async () => {
    await deployCdk(state);
  });

  // ─── Done ───────────────────────────────────────────────────────────────
  displayCompletionSummary();

  const allSucceeded = stepResults.every(r => r.status === 'succeeded');
  if (allSucceeded) {
    banner('Setup Complete! 🎉');

    console.log('  Your Proactive Health Event Impact Analyzer is deployed and ready.');
    console.log('');
    console.log('  Next steps:');
    console.log('    1. Seed the teams table:');
    if (process.platform === 'win32') {
      console.log('       powershell -ExecutionPolicy Bypass -File scripts\\seed-teams.ps1 -TableName health-analyzer-teams');
    } else {
      console.log('       ./scripts/seed-teams.sh health-analyzer-teams');
    }
    console.log('');
    console.log('    2. Upload the DevOps Agent skill:');
    console.log('       See devops-agent-skill/SKILL.md');
    if (state.jiraEnabled) {
      console.log('       (Includes the Jira ticketing step — re-upload after any changes)');
    }
    console.log('');
    console.log('    3. Test with a sample event:');
    if (process.platform === 'win32') {
      console.log(`       aws lambda invoke --function-name EVENT_ROUTER_NAME --payload file://events/test-lambda-nodejs20-lifecycle-event.json --cli-binary-format raw-in-base64-out --region ${state.region} --no-cli-pager output.json`);
    } else {
      console.log(`       aws lambda invoke --function-name EVENT_ROUTER_NAME --payload file://events/test-lambda-nodejs20-lifecycle-event.json --cli-binary-format raw-in-base64-out --region ${state.region} --no-cli-pager /tmp/test-response.json`);
    }
    console.log('');
    if (state.jiraEnabled) {
      console.log('    4. Verify Jira integration:');
      console.log('       - SSM params written:');
      console.log(`         ${SSM_PARAM_JIRA_PROJECT_KEY} = ${state.jiraProjectKey}`);
      console.log(`         ${SSM_PARAM_JIRA_ISSUE_TYPE} = ${state.jiraIssueType}`);
      console.log(`         ${SSM_PARAM_JIRA_SITE_URL} = ${state.jiraSiteUrl}`);
      console.log('       - In the DevOps Agent console, confirm the "atlassian-jira"');
      console.log('         MCP server is associated with your Agent Space.');
      console.log('       - Trigger a test investigation; check Jira project',
        `"${state.jiraProjectKey}" for the auto-created ticket.`);
      console.log('');
      console.log('    5. Monitor in the console:');
    } else {
      console.log('    4. Monitor in the console:');
    }
    console.log(`       Step Functions: https://${state.region}.console.aws.amazon.com/states/home?region=${state.region}`);
    console.log(`       OpsCenter:     https://${state.region}.console.aws.amazon.com/systems-manager/opsitems?region=${state.region}`);
    console.log(`       DevOps Agent:  https://${state.region}.console.aws.amazon.com/aidevops/home?region=${state.region}#/agent-spaces/${state.agentSpaceId}`);
    console.log('');
  }

  rl.close();
}

// ─── Helper Functions ───────────────────────────────────────────────────────

// Lightweight flow that only runs the Jira MCP integration step against
// an already-deployed environment. Skips webhook handling, IAM, operator
// app provisioning, notification channel prompts, and the CDK deploy.
async function runJiraOnly(args: CliOptions): Promise<void> {
  banner('Proactive Health Event Impact Analyzer — Jira-only Setup');

  // Verify CLI + credentials. Mirror the prerequisite checks from the main
  // flow, but skip the CDK availability check (we won't be deploying).
  step(0, 'Checking prerequisites');

  try {
    exec('aws --version', true);
    success('AWS CLI installed');
  } catch {
    console.error('❌ AWS CLI not found. Install it: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html');
    rl.close();
    return;
  }

  const cliVersion = getAwsCliVersion();
  if (cliVersion && compareVersions(cliVersion, MIN_AWS_CLI_VERSION) < 0) {
    console.error(`❌ AWS CLI ${cliVersion} is too old. Need ${MIN_AWS_CLI_VERSION} or newer for 'aws devops-agent'.`);
    rl.close();
    return;
  }
  if (cliVersion) success(`AWS CLI ${cliVersion}`);

  const state: SetupState = {
    region: '',
    accountId: '',
    agentSpaceId: '',
    agentSpaceName: '',
    associationId: '',
    webhookUrl: '',
    webhookSecret: '',
    operatorAppEnabled: false,
    notificationEmail: '',
    slackWebhookUrl: '',
    msTeamsWebhookUrl: '',
    jiraEnabled: false,
    jiraSiteUrl: '',
    jiraProjectKey: '',
    jiraIssueType: 'Task',
    jiraMcpServiceId: '',
    jiraMcpAssociationId: '',
  };

  try {
    const identity = execJson('aws sts get-caller-identity --no-cli-pager');
    state.accountId = identity.Account;
    success(`Authenticated as: ${identity.Arn}`);
    info(`Account: ${state.accountId}`);
  } catch {
    console.error('❌ AWS credentials not configured or session expired.');
    rl.close();
    return;
  }

  // ─── Step 1: Region ─────────────────────────────────────────────────────
  step(1, 'Select AWS Region');

  if (args.region) {
    if (!SUPPORTED_REGIONS.includes(args.region)) {
      console.error(`❌ Region '${args.region}' is not in the supported list: ${SUPPORTED_REGIONS.join(', ')}`);
      rl.close();
      return;
    }
    state.region = args.region;
    success(`Region: ${state.region} (from --region)`);
  } else {
    const regionIdx = await askChoice(
      'Which region is your existing deployment in?',
      SUPPORTED_REGIONS
    );
    state.region = SUPPORTED_REGIONS[regionIdx];
    success(`Region: ${state.region}`);
  }

  // ─── Step 2: Pick the existing Agent Space ──────────────────────────────
  step(2, 'Select existing DevOps Agent Space');

  await pickExistingAgentSpace(state, args.agentSpaceId);

  // ─── Step 3: Run the Jira step ──────────────────────────────────────────
  step(3, 'Atlassian Jira integration');

  await ensureJiraMcp(state, args.jiraTools);

  // ─── Done ───────────────────────────────────────────────────────────────
  banner('Jira Setup Complete! 🎉');

  if (state.jiraEnabled) {
    console.log('  Jira MCP integration is now wired into your existing Agent Space.');
    console.log('');
    console.log('  Next steps:');
    console.log('    1. Re-upload the DevOps Agent skill so the agent picks up Step 6:');
    console.log('       devops-agent-skill/SKILL.md → upload to your agent space.');
    console.log('');
    console.log('    2. SSM params written:');
    console.log(`       ${SSM_PARAM_JIRA_PROJECT_KEY} = ${state.jiraProjectKey}`);
    console.log(`       ${SSM_PARAM_JIRA_ISSUE_TYPE} = ${state.jiraIssueType}`);
    console.log(`       ${SSM_PARAM_JIRA_SITE_URL} = ${state.jiraSiteUrl}`);
    console.log('');
    console.log('    3. Trigger a test investigation. When the agent confirms');
    console.log(`       MEDIUM+ impact, look for a new ticket in project ${state.jiraProjectKey}.`);
  } else {
    console.log('  No changes were made — Jira step was skipped at the prompt.');
  }
  console.log('');

  rl.close();
}

async function pickExistingAgentSpace(state: SetupState, preselectedId?: string): Promise<void> {
  let spaces: any;
  try {
    spaces = execJson(
      `aws devops-agent list-agent-spaces --region ${state.region} --no-cli-pager`
    );
  } catch (error: any) {
    throw new Error(`Could not list Agent Spaces: ${error.message || error}`);
  }

  const list: Array<{ agentSpaceId: string; name: string }> = spaces?.agentSpaces || [];
  if (list.length === 0) {
    throw new Error(`No Agent Spaces found in ${state.region}. Run the full wizard first.`);
  }

  if (preselectedId) {
    const match = list.find((s) => s.agentSpaceId === preselectedId);
    if (!match) {
      throw new Error(`Agent Space '${preselectedId}' not found in ${state.region}.`);
    }
    state.agentSpaceId = match.agentSpaceId;
    state.agentSpaceName = match.name;
    success(`Using Agent Space: ${state.agentSpaceName} (${state.agentSpaceId})`);
    return;
  }

  if (list.length === 1) {
    state.agentSpaceId = list[0].agentSpaceId;
    state.agentSpaceName = list[0].name;
    success(`Using Agent Space: ${state.agentSpaceName} (${state.agentSpaceId})`);
    return;
  }

  const opts = list.map((s) => `${s.name} (${s.agentSpaceId})`);
  const idx = await askChoice('Which Agent Space hosts your existing deployment?', opts);
  state.agentSpaceId = list[idx].agentSpaceId;
  state.agentSpaceName = list[idx].name;
  success(`Using Agent Space: ${state.agentSpaceName}`);
}

async function createAgentSpace(state: SetupState): Promise<void> {
  const name = await ask('  Agent Space name [health-event-analyzer]: ') || 'health-event-analyzer';
  const description = await ask('  Description [Health Event Impact Analyzer]: ') || 'Health Event Impact Analyzer';

  info('Creating Agent Space...');
  const result = execJson(
    `aws devops-agent create-agent-space --name "${name}" --description "${description}" --region ${state.region} --no-cli-pager`
  );

  state.agentSpaceId = result.agentSpace.agentSpaceId;
  state.agentSpaceName = result.agentSpace.name;
  success(`Agent Space created: ${state.agentSpaceName} (${state.agentSpaceId})`);
}

async function ensureIamRoles(state: SetupState): Promise<void> {
  // Check if AgentSpace role exists
  const spaceRoleExists = checkRoleExists('DevOpsAgentRole-AgentSpace');
  const webappRoleExists = checkRoleExists('DevOpsAgentRole-WebappAdmin');

  if (spaceRoleExists && webappRoleExists) {
    success('IAM roles already exist (DevOpsAgentRole-AgentSpace, DevOpsAgentRole-WebappAdmin)');
    return;
  }

  info('Creating IAM roles for DevOps Agent...');

  if (!spaceRoleExists) {
    // Create AgentSpace role — use file:// to avoid shell quoting issues on Windows
    const spaceTrustFile = writeTempJson('space-trust', {
      Version: '2012-10-17',
      Statement: [{
        Effect: 'Allow',
        Principal: { Service: 'aidevops.amazonaws.com' },
        Action: 'sts:AssumeRole',
        Condition: {
          StringEquals: { 'aws:SourceAccount': state.accountId },
          ArnLike: { 'aws:SourceArn': `arn:aws:aidevops:${state.region}:${state.accountId}:agentspace/*` },
        },
      }],
    });

    try {
      exec(
        `aws iam create-role --role-name DevOpsAgentRole-AgentSpace --assume-role-policy-document file://${spaceTrustFile} --no-cli-pager`,
        true
      );
    } finally {
      tryUnlink(spaceTrustFile);
    }

    exec(
      'aws iam attach-role-policy --role-name DevOpsAgentRole-AgentSpace --policy-arn arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy --no-cli-pager',
      true
    );

    // Additional policy for Resource Explorer SLR
    const additionalPolicyFile = writeTempJson('space-slr-policy', {
      Version: '2012-10-17',
      Statement: [{
        Sid: 'AllowCreateServiceLinkedRoles',
        Effect: 'Allow',
        Action: ['iam:CreateServiceLinkedRole'],
        Resource: [`arn:aws:iam::${state.accountId}:role/aws-service-role/resource-explorer-2.amazonaws.com/AWSServiceRoleForResourceExplorer`],
      }],
    });

    try {
      exec(
        `aws iam put-role-policy --role-name DevOpsAgentRole-AgentSpace --policy-name AllowCreateServiceLinkedRoles --policy-document file://${additionalPolicyFile} --no-cli-pager`,
        true
      );
    } finally {
      tryUnlink(additionalPolicyFile);
    }

    success('Created role: DevOpsAgentRole-AgentSpace');
  }

  if (!webappRoleExists) {
    // Create WebappAdmin role — use file:// to avoid shell quoting issues on Windows
    const webappTrustFile = writeTempJson('webapp-trust', {
      Version: '2012-10-17',
      Statement: [{
        Effect: 'Allow',
        Principal: { Service: 'aidevops.amazonaws.com' },
        Action: ['sts:AssumeRole', 'sts:TagSession'],
        Condition: {
          StringEquals: { 'aws:SourceAccount': state.accountId },
          ArnLike: { 'aws:SourceArn': `arn:aws:aidevops:${state.region}:${state.accountId}:agentspace/*` },
        },
      }],
    });

    try {
      exec(
        `aws iam create-role --role-name DevOpsAgentRole-WebappAdmin --assume-role-policy-document file://${webappTrustFile} --no-cli-pager`,
        true
      );
    } finally {
      tryUnlink(webappTrustFile);
    }

    exec(
      'aws iam attach-role-policy --role-name DevOpsAgentRole-WebappAdmin --policy-arn arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy --no-cli-pager',
      true
    );

    success('Created role: DevOpsAgentRole-WebappAdmin');
  }

  // Wait for IAM propagation
  info('Waiting for IAM role propagation (10s)...');
  await new Promise((resolve) => setTimeout(resolve, 10000));
}

function checkRoleExists(roleName: string): boolean {
  try {
    exec(`aws iam get-role --role-name ${roleName} --no-cli-pager`, true);
    return true;
  } catch {
    return false;
  }
}

async function ensureAccountAssociation(state: SetupState): Promise<void> {
  // Check existing associations
  const associations = execJson(
    `aws devops-agent list-associations --agent-space-id ${state.agentSpaceId} --region ${state.region} --no-cli-pager`
  );

  const awsAssociation = associations.associations?.find(
    (a: any) => a.configuration?.aws?.accountId === state.accountId
  );

  if (awsAssociation) {
    state.associationId = awsAssociation.associationId;
    success(`Account ${state.accountId} already associated (${state.associationId})`);
    return;
  }

  info(`Associating account ${state.accountId} for topology discovery...`);

  const configFile = writeTempJson('assoc-config', {
    aws: {
      assumableRoleArn: `arn:aws:iam::${state.accountId}:role/DevOpsAgentRole-AgentSpace`,
      accountId: state.accountId,
      accountType: 'monitor',
    },
  });

  let result: any;
  try {
    result = execJson(
      `aws devops-agent associate-service --agent-space-id ${state.agentSpaceId} --service-id aws --configuration file://${configFile} --region ${state.region} --no-cli-pager`
    );
  } finally {
    tryUnlink(configFile);
  }

  state.associationId = result.association.associationId;
  success(`Account associated: ${state.associationId} (status: ${result.association.status})`);
}

async function ensureWebhook(state: SetupState): Promise<void> {
  // Check existing associations for eventChannel webhooks
  const associations = execJson(
    `aws devops-agent list-associations --agent-space-id ${state.agentSpaceId} --region ${state.region} --no-cli-pager`
  );

  // Look for existing eventChannel associations and their webhooks
  const eventChannelAssociations = associations.associations?.filter(
    (a: any) => a.configuration?.eventChannel !== undefined
  ) || [];

  if (eventChannelAssociations.length > 0) {
    // The DevOps Agent service allows only ONE eventChannel association per
    // AgentSpace. We must either reuse it or rotate it (disassociate + recreate).
    const assoc = eventChannelAssociations[0];

    let existingUrl: string | undefined;
    try {
      const webhooks = execJson(
        `aws devops-agent list-webhooks --agent-space-id ${state.agentSpaceId} --association-id ${assoc.associationId} --region ${state.region} --no-cli-pager`
      );
      existingUrl = webhooks.webhooks?.[0]?.webhookUrl;
    } catch {
      // list-webhooks failed; treat as no usable webhook found.
    }

    if (existingUrl) {
      info(`Found existing eventChannel webhook: ${existingUrl.substring(0, 60)}...`);

      const choices = [
        'Reuse the existing webhook (I have or can recover the HMAC secret)',
        'Rotate it: delete this association and create a fresh one (new URL + new secret)',
      ];
      const choice = await askChoice('How do you want to proceed?', choices);

      if (choice === 0) {
        state.webhookUrl = existingUrl;
        const recovered = tryRecoverSecretFromDeployedStack(state);
        if (recovered) {
          state.webhookSecret = recovered;
          success('Recovered HMAC secret from a previously deployed stack.');
          return;
        }

        warn('AWS does not expose the HMAC secret after creation. It is only returned once');
        warn('by associate-service. Look in any of these places for the previous value:');
        console.log('    • The DEVOPS_AGENT_WEBHOOK_SECRET env var on the InvestigationTrigger Lambda');
        console.log('    • The CloudFormation parameter DevOpsAgentWebhookSecret on a previous deployment');
        console.log('    • Wherever you stored it during the prior setup (password manager / SSM / etc.)');
        console.log('  If it is truly lost, re-run this wizard and choose "Rotate" instead.');
        const provided = await ask('  Paste the existing HMAC secret (or leave blank to abort): ');
        if (!provided) {
          throw new Error('Cannot continue without the existing webhook secret. Re-run and choose "Rotate" to generate a new one.');
        }
        state.webhookSecret = provided;
        success(`Using existing webhook: ${state.webhookUrl.substring(0, 60)}...`);
        return;
      }

      // Rotate path: disassociate the existing eventChannel, then fall through
      // to the create branch below.
      const confirmRotate = await askYesNo(
        `  Confirm: delete the existing eventChannel association ${assoc.associationId} and generate a new webhook?`,
        false
      );
      if (!confirmRotate) {
        throw new Error('Rotation cancelled. Re-run the wizard when ready.');
      }

      info(`Disassociating existing eventChannel (${assoc.associationId})...`);
      exec(
        `aws devops-agent disassociate-service --agent-space-id ${state.agentSpaceId} --association-id ${assoc.associationId} --region ${state.region} --no-cli-pager`,
        true
      );
      success('Old eventChannel association removed.');
      // Brief pause to let the deletion settle before recreating.
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
  }

  // Create a new webhook via eventChannel
  info('Creating a new generic webhook...');

  // First, register the eventChannel service
  let serviceId: string;
  const serviceDetailsFile = writeTempJson('event-channel-details', { eventChannel: {} });
  try {
    const registerResult = execJson(
      `aws devops-agent register-service --service eventChannel --service-details file://${serviceDetailsFile} --region ${state.region} --no-cli-pager`
    );
    serviceId = registerResult.serviceId;
  } catch (error: any) {
    // Service might already be registered, try to find it
    const services = execJson(
      `aws devops-agent list-services --region ${state.region} --no-cli-pager`
    );
    const eventChannelService = services.services?.find((s: any) => s.serviceType === 'eventChannel');
    if (eventChannelService) {
      serviceId = eventChannelService.serviceId;
    } else {
      throw new Error('Failed to register eventChannel service');
    }
  } finally {
    tryUnlink(serviceDetailsFile);
  }

  // Associate the eventChannel to generate the webhook
  const eventChannelConfigFile = writeTempJson('event-channel-config', { eventChannel: {} });
  let result: any;
  try {
    result = execJson(
      `aws devops-agent associate-service --agent-space-id ${state.agentSpaceId} --service-id ${serviceId} --configuration file://${eventChannelConfigFile} --region ${state.region} --no-cli-pager`
    );
  } finally {
    tryUnlink(eventChannelConfigFile);
  }

  if (result.webhook) {
    state.webhookUrl = result.webhook.webhookUrl;
    state.webhookSecret = result.webhook.webhookSecret;
    success(`Webhook created: ${state.webhookUrl}`);
    info(`Secret: ${state.webhookSecret.substring(0, 10)}... (stored securely for deployment)`);
  } else {
    throw new Error('Webhook was not generated in the association response');
  }
}

/**
 * Best-effort recovery of the HMAC secret from a previously deployed
 * HealthEventAnalyzerStack-<region>. The wizard's CDK stack stores the secret
 * as a Lambda environment variable (DEVOPS_AGENT_WEBHOOK_SECRET) on the
 * InvestigationTrigger function. The CFN parameter is `noEcho`, so this is
 * the only place it is recoverable.
 *
 * Returns the secret string on success, or undefined if it can't be recovered
 * (e.g. stack not deployed yet, function not found, missing env var).
 */
function tryRecoverSecretFromDeployedStack(state: SetupState): string | undefined {
  const stackName = `HealthEventAnalyzerStack-${state.region}`;
  let stackResources: any[];
  try {
    const resp = execJson(
      `aws cloudformation list-stack-resources --stack-name ${stackName} --region ${state.region} --no-cli-pager`
    );
    stackResources = resp.StackResourceSummaries || [];
  } catch {
    return undefined;
  }

  const triggerLambda = stackResources.find(
    (r: any) =>
      r.ResourceType === 'AWS::Lambda::Function' &&
      typeof r.LogicalResourceId === 'string' &&
      r.LogicalResourceId.includes('InvestigationTrigger')
  );
  if (!triggerLambda?.PhysicalResourceId) return undefined;

  try {
    const fn = execJson(
      `aws lambda get-function-configuration --function-name ${triggerLambda.PhysicalResourceId} --region ${state.region} --no-cli-pager`
    );
    const secret = fn?.Environment?.Variables?.DEVOPS_AGENT_WEBHOOK_SECRET;
    return typeof secret === 'string' && secret.length > 0 ? secret : undefined;
  } catch {
    return undefined;
  }
}

async function ensureOperatorApp(state: SetupState): Promise<void> {
  try {
    const operatorApp = execJson(
      `aws devops-agent get-operator-app --agent-space-id ${state.agentSpaceId} --region ${state.region} --no-cli-pager`
    );
    if (operatorApp) {
      success('Operator app already enabled');
      state.operatorAppEnabled = true;
      return;
    }
  } catch {
    // Not enabled yet
  }

  info('Enabling operator app...');
  exec(
    `aws devops-agent enable-operator-app --agent-space-id ${state.agentSpaceId} --auth-flow iam --operator-app-role-arn "arn:aws:iam::${state.accountId}:role/DevOpsAgentRole-WebappAdmin" --region ${state.region} --no-cli-pager`,
    true
  );
  state.operatorAppEnabled = true;
  success('Operator app enabled (IAM auth)');
}

// ─── Atlassian Jira MCP integration ─────────────────────────────────────────

async function ensureJiraMcp(state: SetupState, toolsOverride?: string[]): Promise<void> {
  console.log('  The DevOps Agent can create and comment on Jira tickets when it');
  console.log('  detects MEDIUM or higher impact on a Health event.');
  console.log('  This is opt-in. Skip if you don\'t use Jira.');
  console.log('');
  const want = await askYesNo('  Configure Atlassian Jira integration?', false);
  if (!want) {
    skipped('Jira integration skipped — agent will not create tickets.');
    return;
  }

  console.log('');
  console.log('  Before continuing, make sure you have:');
  console.log('    1. A Jira Cloud site (free tier is fine):');
  console.log('       https://www.atlassian.com/try/cloud/signup');
  console.log('    2. A SCOPED API token — IMPORTANT:');
  console.log('       https://id.atlassian.com/manage-profile/security/api-tokens');
  console.log('       • Click "Create API token with scopes" (NOT plain "Create API token")');
  console.log('       • App = "Rovo MCP"');
  console.log('       • Tick at minimum: read:jira-work, write:jira-work, search:jira-work');
  console.log('       (a plain unscoped token only exposes Teamwork Graph and will fail)');
  console.log('    3. In Atlassian Admin → Rovo → Rovo MCP server:');
  console.log('       • Authentication tab → "Allow API token authentication" = on');
  console.log('       • Permissions tab → Read, Write, Search rows = Allowed for Jira');
  console.log('         (without this, the server exposes 0 Jira tools to your token)');
  console.log('       (org admin only — on a fresh free site you are the admin)');
  console.log('  Full guide: docs/jira-integration.md');
  console.log('');

  // Detect existing registration up front so we can drive the prompts
  // (reuse vs. rotate) and decide whether to ask for credentials.
  const existing = findExistingJiraMcpRegistration(state.region);
  let reusing = false;
  if (existing) {
    info(`Found existing MCP registration: ${ATLASSIAN_MCP_NAME} (${existing.serviceId})`);
    reusing = await askYesNo(
      '  Reuse this registration (recommended) instead of rotating the API token?',
      true
    );
    if (reusing) {
      state.jiraMcpServiceId = existing.serviceId;
    } else {
      info('Deregistering existing Jira MCP server to rotate credentials...');
      await disassociateAllJiraMcp(state.region, existing.serviceId);
      try {
        exec(
          `aws devops-agent deregister-service --service-id ${existing.serviceId} --region ${state.region} --no-cli-pager`,
          true
        );
        success('Old registration removed');
      } catch {
        warn('Failed to deregister old MCP server — continuing anyway');
      }
      // Reset serviceId so the wizard re-registers below
      state.jiraMcpServiceId = '';
      // Wait briefly to avoid name collision on re-register
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
  }

  // Collect Jira tenant details. Always required.
  state.jiraSiteUrl = await askJiraSiteUrl();
  const projectKeyPrompt = await ask('  Jira project key for tickets [OPS]: ');
  state.jiraProjectKey = (projectKeyPrompt || 'OPS').toUpperCase();
  const issueTypePrompt = await ask('  Default issue type [Task]: ');
  state.jiraIssueType = issueTypePrompt || 'Task';

  // Collect credentials. We need them when registering, AND when probing
  // the live MCP server for tool discovery (to avoid hardcoded tool names
  // that drift). Skip the credential prompt only if we already have a
  // registration to reuse AND the operator passed --jira-tools (in which
  // case we don't need to talk to the server at all).
  let basicAuthValue = '';
  const needCreds = !reusing || !(toolsOverride && toolsOverride.length > 0);
  if (needCreds) {
    const promptReason = reusing
      ? '  (Used to discover the actual tool names from your Atlassian server.)'
      : '  (Used as Basic auth for the MCP server registration.)';
    console.log(promptReason);
    const email = await ask('  Atlassian account email: ');
    if (!email) throw new Error('Atlassian email is required.');
    const apiToken = await ask(
      '  Atlassian API token (paste it; treat your terminal scrollback accordingly): '
    );
    if (!apiToken) throw new Error('Atlassian API token is required.');
    // The Atlassian Rovo MCP API token flow uses HTTP Basic auth:
    //   Authorization: Basic base64(email:token)
    // Source: https://support.atlassian.com/rovo/docs/authentication-and-authorization/
    basicAuthValue =
      'Basic ' + Buffer.from(`${email}:${apiToken}`, 'utf-8').toString('base64');
  }

  // Discover the real tool names from the live server. This is the part
  // that makes the wizard portable: the public docs use bare names like
  // `getJiraIssue`, but AWS DevOps Agent's tool-discovery handshake may
  // see them under a different prefix scheme. Probing means we always
  // hand AWS exactly what AWS will accept.
  let tools: string[];
  if (toolsOverride && toolsOverride.length > 0) {
    tools = toolsOverride;
    info(`Using --jira-tools override: ${tools.join(', ')}`);
  } else {
    info('Discovering available tools from the Atlassian MCP server...');
    let advertised: McpTool[] = [];
    try {
      advertised = await listMcpTools(ATLASSIAN_MCP_ENDPOINT, basicAuthValue);
    } catch (e: any) {
      console.error('');
      console.error('  ❌ Could not probe the Atlassian MCP server for its tool list.');
      console.error(`     ${e.message}`);
      console.error('');
      console.error('  Falling back to the documented default tool names. If the next');
      console.error('  step fails with a "tools are not available" error, see the');
      console.error('  console-discovery instructions below.');
      tools = ATLASSIAN_MCP_DEFAULT_TOOLS;
    }
    if (advertised.length > 0) {
      success(`Discovered ${advertised.length} tool(s) from the server.`);
      const { matched, missing } = selectMatchingTools(advertised, ATLASSIAN_TOOL_PATTERNS);
      if (matched.length === 0) {
        console.error('');
        console.error('  ❌ The MCP server returned tools, but none matched our intent');
        console.error('     (read/search/create/comment Jira). Tools advertised:');
        for (const t of advertised.slice(0, 30)) {
          console.error(`       - ${t.name}`);
        }
        if (advertised.length > 30) {
          console.error(`       ... and ${advertised.length - 30} more.`);
        }
        console.error('');
        const onlyTeamworkGraph = advertised.every((t) =>
          /TeamworkGraph/i.test(t.name)
        );
        if (onlyTeamworkGraph) {
          console.error('  All discovered tools are from the Teamwork Graph permission group.');
          console.error('  This means the Jira permission groups are not allowed for your');
          console.error('  Atlassian organization yet. Fix: in Atlassian Admin → Rovo →');
          console.error('  Rovo MCP server → Permissions tab, set Read, Write, and Search');
          console.error('  to Allowed (or use Edit details to tick Jira on each row).');
          console.error('  Changes take effect immediately. Then re-run this wizard.');
        } else {
          console.error('  Re-run with --jira-tools "name1,name2,..." picking from the list above.');
        }
        throw new Error('No matching tools discovered from Atlassian MCP server.');
      }
      tools = matched;
      info(`Selected ${matched.length} matching tool(s): ${matched.join(', ')}`);
      if (missing.length > 0) {
        warn(
          `Could not find a match for ${missing.length} desired tool pattern(s). The integration will still work with ${matched.length} tools.`
        );
      }
    } else {
      warn('Server reported zero tools. Falling back to defaults — AWS will likely reject this.');
      tools = ATLASSIAN_MCP_DEFAULT_TOOLS;
    }
  }

  // Register the MCP server if we don't have a service id yet.
  if (!state.jiraMcpServiceId) {
    if (!basicAuthValue) {
      throw new Error('Internal error: missing credentials at register step.');
    }
    const registerPayload = {
      mcpserver: {
        name: ATLASSIAN_MCP_NAME,
        endpoint: ATLASSIAN_MCP_ENDPOINT,
        description: 'Atlassian Rovo MCP Server — Jira integration for Health event tickets',
        authorizationConfig: {
          apiKey: {
            apiKeyName: 'atlassian-api-token',
            apiKeyValue: basicAuthValue,
            apiKeyHeader: 'Authorization',
          },
        },
      },
    };

    info('Registering Atlassian Jira MCP server with DevOps Agent...');
    const tmpFile = writeTempJson('register-mcpserver', registerPayload);
    try {
      const result = execJson(
        `aws devops-agent register-service --service mcpserver --name "${ATLASSIAN_MCP_NAME}" --service-details file://${tmpFile} --region ${state.region} --no-cli-pager`
      );
      if (!result.serviceId) {
        throw new Error(
          `register-service did not return a serviceId. Response: ${JSON.stringify(result)}`
        );
      }
      state.jiraMcpServiceId = result.serviceId;
      success(`MCP server registered (id: ${state.jiraMcpServiceId})`);
    } finally {
      tryUnlink(tmpFile);
    }
  }

  // Associate with the Agent Space (idempotent: associate-service overwrites
  // an existing same-service association per the API contract).
  const associatePayload = { mcpserver: { tools } };

  info(
    `Associating MCP server with Agent Space and allow-listing ${tools.length} tool(s)...`
  );
  const associateTmp = writeTempJson('associate-mcpserver', associatePayload);
  try {
    const result = execJson(
      `aws devops-agent associate-service --agent-space-id ${state.agentSpaceId} --service-id ${state.jiraMcpServiceId} --configuration file://${associateTmp} --region ${state.region} --no-cli-pager`
    );
    state.jiraMcpAssociationId = result.association?.associationId || '';
    success(
      `MCP server associated with Agent Space (status: ${result.association?.status || 'unknown'})`
    );
  } catch (err: any) {
    const stderr: string = (err?.stderr || err?.message || '').toString();
    if (/tools are not available in MCP server/i.test(stderr)) {
      console.error('');
      console.error('  ❌ AWS rejected the tool list. The names we sent don\'t match');
      console.error('     what AWS DevOps Agent discovered when it introspected the server.');
      console.error('');
      console.error('  Most common cause: no human has yet completed an OAuth 3LO consent');
      console.error('  for the MCP app on this Atlassian site. Atlassian requires the');
      console.error('  first user to consent via OAuth before API-token clients can see');
      console.error('  the full tool surface. Fix: open Claude Desktop, VS Code with the');
      console.error('  MCP extension, or any other supported client and complete one');
      console.error('  OAuth login against:');
      console.error('     https://mcp.atlassian.com/v1/mcp/authv2');
      console.error('  Then re-run this wizard.');
      console.error('');
      console.error('  Alternative: discover the names AWS sees in the AWS console:');
      console.error(`     https://${state.region}.console.aws.amazon.com/aidevops/home?region=${state.region}#/agent-spaces/${state.agentSpaceId}`);
      console.error('     → Capabilities tab → MCP Servers → Add → pick "atlassian-jira"');
      console.error('     → "Select specific tools" — the dropdown shows the real names.');
      console.error('  Then re-run with --jira-tools "name1,name2,..." to apply them.');
      console.error('');
      console.error('  The MCP server is registered (no need to re-register). The wizard\'s');
      console.error('  reuse path will skip registration on the next run.');
      console.error('');
    }
    throw err;
  } finally {
    tryUnlink(associateTmp);
  }

  // Persist non-secret routing info to SSM so the agent skill can read it
  // at runtime without redeploying the stack on changes.
  info('Writing Jira routing config to SSM Parameter Store...');
  putSsmParam(
    state.region,
    SSM_PARAM_JIRA_PROJECT_KEY,
    state.jiraProjectKey,
    'Jira project key used by Health Event Analyzer for auto-created tickets'
  );
  putSsmParam(
    state.region,
    SSM_PARAM_JIRA_ISSUE_TYPE,
    state.jiraIssueType,
    'Default Jira issue type used by Health Event Analyzer'
  );
  putSsmParam(
    state.region,
    SSM_PARAM_JIRA_SITE_URL,
    state.jiraSiteUrl,
    'Atlassian Cloud site URL referenced in auto-created tickets'
  );
  success(
    `SSM params written: ${SSM_PARAM_JIRA_PROJECT_KEY}, ${SSM_PARAM_JIRA_ISSUE_TYPE}, ${SSM_PARAM_JIRA_SITE_URL}`
  );

  // The trigger Lambda already has IAM permission to read these params (via
  // the CDK construct) and will inline them into the prompt sent to the
  // agent. We do NOT add an inline policy on DevOpsAgentRole-AgentSpace —
  // the agent's session policy strips its role's SSM permissions, so an
  // inline grant doesn't help.

  state.jiraEnabled = true;
}

async function askJiraSiteUrl(): Promise<string> {
  for (;;) {
    const raw = (await ask('  Atlassian site URL (e.g. https://acme.atlassian.net): ')).trim();
    if (!raw) {
      warn('Site URL is required.');
      continue;
    }
    let url = raw;
    if (!/^https?:\/\//i.test(url)) {
      url = 'https://' + url;
    }
    if (!/^https:\/\/[a-z0-9-]+\.atlassian\.net\/?$/i.test(url)) {
      warn('Expected something like https://your-site.atlassian.net — try again.');
      continue;
    }
    return url.replace(/\/$/, '');
  }
}

interface ServiceSummary {
  serviceId: string;
  name?: string;
  serviceType?: string;
}

function findExistingJiraMcpRegistration(region: string): ServiceSummary | undefined {
  let services: any;
  try {
    services = execJson(
      `aws devops-agent list-services --region ${region} --no-cli-pager`
    );
  } catch {
    return undefined;
  }
  const list: ServiceSummary[] = services?.services || [];
  return list.find(
    (s) =>
      (s.serviceType === 'mcpserver' || s.serviceType === undefined) &&
      typeof s.name === 'string' &&
      s.name === ATLASSIAN_MCP_NAME
  );
}

async function disassociateAllJiraMcp(region: string, serviceId: string): Promise<void> {
  // Walk every Agent Space and remove any associations referencing this
  // service id. Required before deregister-service.
  let spaces: any;
  try {
    spaces = execJson(`aws devops-agent list-agent-spaces --region ${region} --no-cli-pager`);
  } catch {
    return;
  }
  for (const space of spaces?.agentSpaces || []) {
    let assocs: any;
    try {
      assocs = execJson(
        `aws devops-agent list-associations --agent-space-id ${space.agentSpaceId} --region ${region} --no-cli-pager`
      );
    } catch {
      continue;
    }
    for (const a of assocs?.associations || []) {
      if (a.serviceId === serviceId) {
        try {
          exec(
            `aws devops-agent disassociate-service --agent-space-id ${space.agentSpaceId} --association-id ${a.associationId} --region ${region} --no-cli-pager`,
            true
          );
        } catch {
          // best-effort
        }
      }
    }
  }
}

function putSsmParam(region: string, name: string, value: string, description: string): void {
  // Use a temp file for --value to avoid shell-escaping the value, and
  // --overwrite so re-runs are idempotent.
  const fs = require('fs') as typeof import('fs');
  const os = require('os') as typeof import('os');
  const pathMod = require('path') as typeof import('path');
  const tmp = pathMod.join(os.tmpdir(), `ssm-param-${process.pid}-${Date.now()}.txt`);
  fs.writeFileSync(tmp, value, { encoding: 'utf-8', mode: 0o600 });
  try {
    exec(
      `aws ssm put-parameter --name "${name}" --type String --overwrite --description "${description.replace(/"/g, '\\"')}" --value file://${tmp} --region ${region} --no-cli-pager`,
      true
    );
  } finally {
    tryUnlink(tmp);
  }
}

function putSsmSecureString(region: string, name: string, value: string, description: string): void {
  // Stores secrets as SecureString (encrypted at rest with default KMS key).
  // Uses a temp file for --value to avoid shell-escaping, --overwrite for idempotency.
  const fs = require('fs') as typeof import('fs');
  const os = require('os') as typeof import('os');
  const pathMod = require('path') as typeof import('path');
  const tmp = pathMod.join(os.tmpdir(), `ssm-secret-${process.pid}-${Date.now()}.txt`);
  fs.writeFileSync(tmp, value, { encoding: 'utf-8', mode: 0o600 });
  try {
    exec(
      `aws ssm put-parameter --name "${name}" --type SecureString --overwrite --description "${description.replace(/"/g, '\\"')}" --value file://${tmp} --region ${region} --no-cli-pager`,
      true
    );
  } finally {
    tryUnlink(tmp);
  }
}

function writeTempJson(prefix: string, payload: unknown): string {
  const fs = require('fs') as typeof import('fs');
  const os = require('os') as typeof import('os');
  const pathMod = require('path') as typeof import('path');
  const file = pathMod.join(os.tmpdir(), `${prefix}-${process.pid}-${Date.now()}.json`);
  fs.writeFileSync(file, JSON.stringify(payload), { encoding: 'utf-8', mode: 0o600 });
  return file;
}

function tryUnlink(file: string): void {
  try {
    const fs = require('fs') as typeof import('fs');
    fs.unlinkSync(file);
  } catch {
    // ignore
  }
}

// ─── MCP tool discovery ─────────────────────────────────────────────────────

interface McpTool {
  name: string;
  description?: string;
}

/**
 * Probe an MCP server over Streamable HTTP using the canonical
 * `initialize` → `tools/list` handshake. Used to discover the actual
 * tool names the server advertises, so the wizard can hand AWS DevOps
 * Agent a list that's guaranteed to match what the server exposes —
 * no matter what naming scheme the server uses on the wire.
 *
 * The Atlassian Rovo MCP server returns SSE-framed responses
 * (Content-Type: text/event-stream); this parser accepts both SSE and
 * plain JSON bodies.
 */
async function listMcpTools(
  endpoint: string,
  authHeaderValue: string
): Promise<McpTool[]> {
  const https = require('https') as typeof import('https');
  const { URL } = require('url') as typeof import('url');
  const u = new URL(endpoint);
  const baseHeaders: Record<string, string> = {
    Authorization: authHeaderValue,
    'Content-Type': 'application/json',
    Accept: 'application/json, text/event-stream',
    'MCP-Protocol-Version': '2025-03-26',
  };

  const sessionRef: { id?: string } = {};

  const post = (body: unknown): Promise<{ status: number; payload: any; headers: Record<string, string> }> =>
    new Promise((resolve, reject) => {
      const data = JSON.stringify(body);
      const headers: Record<string, string> = {
        ...baseHeaders,
        'Content-Length': String(Buffer.byteLength(data)),
      };
      if (sessionRef.id) headers['Mcp-Session-Id'] = sessionRef.id;

      const req = https.request(
        {
          method: 'POST',
          hostname: u.hostname,
          port: u.port || 443,
          path: u.pathname + (u.search || ''),
          headers,
        },
        (res) => {
          const chunks: Buffer[] = [];
          res.on('data', (c: Buffer) => chunks.push(c));
          res.on('end', () => {
            const respHeaders: Record<string, string> = {};
            for (const [k, v] of Object.entries(res.headers)) {
              if (typeof v === 'string') respHeaders[k.toLowerCase()] = v;
              else if (Array.isArray(v)) respHeaders[k.toLowerCase()] = v.join(', ');
            }
            const sid = respHeaders['mcp-session-id'];
            if (sid) sessionRef.id = sid;

            const text = Buffer.concat(chunks).toString('utf-8');
            const ct = respHeaders['content-type'] || '';
            try {
              if (ct.includes('text/event-stream')) {
                const payload = parseSsePayload(text);
                resolve({ status: res.statusCode || 0, payload, headers: respHeaders });
              } else if (ct.includes('application/json') || text.trim().startsWith('{')) {
                resolve({ status: res.statusCode || 0, payload: JSON.parse(text), headers: respHeaders });
              } else if (text.trim().length === 0) {
                resolve({ status: res.statusCode || 0, payload: undefined, headers: respHeaders });
              } else {
                resolve({ status: res.statusCode || 0, payload: { _raw: text }, headers: respHeaders });
              }
            } catch (e) {
              reject(new Error(`MCP probe parse error (${ct}, status ${res.statusCode}): ${(e as Error).message}\n${text.slice(0, 500)}`));
            }
          });
        }
      );
      req.on('error', reject);
      req.setTimeout(15000, () => req.destroy(new Error('MCP probe timed out')));
      req.write(data);
      req.end();
    });

  // 1) initialize
  const initResp = await post({
    jsonrpc: '2.0',
    id: 1,
    method: 'initialize',
    params: {
      protocolVersion: '2025-03-26',
      capabilities: {},
      clientInfo: { name: 'health-event-analyzer-setup-wizard', version: '1.0.0' },
    },
  });
  if (initResp.status >= 400 || initResp.payload?.error) {
    const msg =
      initResp.payload?.error?.message ||
      initResp.payload?._raw?.slice?.(0, 200) ||
      `HTTP ${initResp.status}`;
    throw new Error(`MCP initialize failed: ${msg}`);
  }

  // The MCP spec requires a notifications/initialized message before
  // further requests. The Atlassian server tolerates either pattern, but
  // sending it keeps us spec-compliant.
  try {
    await post({ jsonrpc: '2.0', method: 'notifications/initialized' });
  } catch {
    // notification-only; the server is allowed to return 202/204 and we
    // don't strictly need to confirm it.
  }

  // 2) tools/list
  const listResp = await post({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} });
  if (listResp.status >= 400 || listResp.payload?.error) {
    const msg =
      listResp.payload?.error?.message ||
      listResp.payload?._raw?.slice?.(0, 200) ||
      `HTTP ${listResp.status}`;
    throw new Error(`MCP tools/list failed: ${msg}`);
  }

  const toolList = listResp.payload?.result?.tools;
  if (!Array.isArray(toolList)) {
    throw new Error(
      `MCP tools/list returned unexpected payload: ${JSON.stringify(listResp.payload).slice(0, 300)}`
    );
  }
  return toolList
    .filter((t) => t && typeof t.name === 'string')
    .map((t) => ({ name: t.name, description: t.description }));
}

/**
 * Extract the first JSON object embedded in an SSE message body.
 * Streamable-HTTP responses look like:
 *   event: message
 *   data: {"jsonrpc":"2.0",...}\n\n
 */
function parseSsePayload(body: string): any {
  for (const line of body.split(/\r?\n/)) {
    if (line.startsWith('data:')) {
      const json = line.slice(5).trim();
      if (json && json !== '[DONE]') {
        return JSON.parse(json);
      }
    }
  }
  throw new Error('SSE response did not contain a data: payload');
}

/**
 * Pick the subset of advertised tool names that match our desired
 * Jira capability set, preserving the order in `patterns`. If a pattern
 * matches multiple advertised names, we keep the shortest (i.e. the
 * least-prefixed) name as the canonical match.
 */
function selectMatchingTools(
  advertised: McpTool[],
  patterns: RegExp[]
): { matched: string[]; missing: RegExp[] } {
  const matched: string[] = [];
  const missing: RegExp[] = [];
  for (const pat of patterns) {
    const candidates = advertised
      .map((t) => t.name)
      .filter((n) => pat.test(n))
      .sort((a, b) => a.length - b.length);
    if (candidates.length > 0) matched.push(candidates[0]);
    else missing.push(pat);
  }
  return { matched, missing };
}

async function deployCdk(state: SetupState): Promise<void> {
  const cdkDir = path.resolve(__dirname, '../infrastructure/cdk');

  // Install dependencies
  info('Installing CDK dependencies...');
  execSync('npm install --silent', { cwd: cdkDir, encoding: 'utf-8', stdio: 'pipe' });
  success('Dependencies installed');

  // Bootstrap (idempotent)
  info('Bootstrapping CDK environment...');
  try {
    const bootstrapOutput = execSync(
      `npx cdk bootstrap aws://${state.accountId}/${state.region}`,
      { cwd: cdkDir, encoding: 'utf-8', stdio: 'pipe', env: { ...process.env, AWS_REGION: state.region, AWS_DEFAULT_REGION: state.region, CDK_DEFAULT_REGION: state.region, CDK_DEFAULT_ACCOUNT: state.accountId } }
    );
    // Check if output contains success indicators
    if (bootstrapOutput.includes('already bootstrapped') || bootstrapOutput.includes('Bootstrapping environment') || bootstrapOutput.includes('✅')) {
      success('CDK environment bootstrapped');
    } else {
      success('CDK environment bootstrapped');
    }
  } catch (bootstrapError: any) {
    const errMsg = bootstrapError?.stderr || bootstrapError?.stdout || bootstrapError?.message || '';
    // "already bootstrapped" or "No changes" are fine — anything else is a real failure
    if (errMsg.includes('already bootstrapped') || errMsg.includes('No changes')) {
      success('CDK environment already bootstrapped');
    } else {
      warn(`CDK bootstrap failed — retrying with explicit environment...`);
      // Retry with explicit --trust and --cloudformation-execution-policies
      try {
        execSync(
          `npx cdk bootstrap aws://${state.accountId}/${state.region} --trust ${state.accountId} --cloudformation-execution-policies arn:aws:iam::aws:policy/AdministratorAccess`,
          { cwd: cdkDir, encoding: 'utf-8', stdio: 'inherit', env: { ...process.env, AWS_REGION: state.region, AWS_DEFAULT_REGION: state.region, CDK_DEFAULT_REGION: state.region, CDK_DEFAULT_ACCOUNT: state.accountId } }
        );
        success('CDK environment bootstrapped (retry succeeded)');
      } catch (retryError: any) {
        throw new Error(
          `CDK bootstrap failed for aws://${state.accountId}/${state.region}. ` +
          `Run manually: npx cdk bootstrap aws://${state.accountId}/${state.region}\n` +
          `Error: ${retryError?.stderr || retryError?.message || retryError}`
        );
      }
    }
  }

  // Build deploy command — enforce --require-approval broadening for production safety
  const stackName = `HealthEventAnalyzerStack-${state.region}`;
  const params: string[] = [
    `--parameters DevOpsAgentWebhookUrl=${state.webhookUrl}`,
  ];

  if (state.notificationEmail) {
    params.push(`--parameters NotificationEmail=${state.notificationEmail}`);
  }

  // Store secrets in SSM Parameter Store SecureString (not as CFn parameters)
  const ssmPrefix = `/health-analyzer/production`;
  info('Storing secrets in SSM Parameter Store...');

  if (state.webhookSecret) {
    putSsmSecureString(state.region, `${ssmPrefix}/webhook-secret`, state.webhookSecret,
      'DevOps Agent HMAC webhook secret');
    success(`SSM: ${ssmPrefix}/webhook-secret`);
  }
  if (state.slackWebhookUrl) {
    putSsmSecureString(state.region, `${ssmPrefix}/slack-webhook-url`, state.slackWebhookUrl,
      'Slack incoming webhook URL for notifications');
    success(`SSM: ${ssmPrefix}/slack-webhook-url`);
  }
  if (state.msTeamsWebhookUrl) {
    putSsmSecureString(state.region, `${ssmPrefix}/msteams-webhook-url`, state.msTeamsWebhookUrl,
      'Microsoft Teams webhook URL for notifications');
    success(`SSM: ${ssmPrefix}/msteams-webhook-url`);
  }

  const deployCmd = [
    'npx cdk deploy',
    '--all',
    ...params,
    '--require-approval=broadening',
  ].join(' ');

  info(`Deploying stack: ${stackName}`);
  console.log('');

  try {
    execSync(deployCmd, {
      cwd: cdkDir,
      encoding: 'utf-8',
      stdio: 'inherit',
      env: { ...process.env, AWS_REGION: state.region, AWS_DEFAULT_REGION: state.region },
    });
    success(`Stack deployed: ${stackName}`);
  } catch (error: any) {
    throw new Error(`CDK deployment failed for stack ${stackName}. Check the output above for details.`);
  }

  // Print outputs
  try {
    const outputs = execJson(
      `aws cloudformation describe-stacks --stack-name ${stackName} --region ${state.region} --query "Stacks[0].Outputs" --no-cli-pager`
    );
    console.log('\n  Stack Outputs:');
    for (const output of outputs || []) {
      console.log(`    ${output.OutputKey}: ${output.OutputValue}`);
    }
  } catch {
    // Non-critical
  }
}

// ─── Entry Point ────────────────────────────────────────────────────────────

main().catch((error) => {
  console.error('\n❌ Setup wizard encountered an unexpected error:', error?.message || error);
  displayCompletionSummary();
  rl.close();
});
