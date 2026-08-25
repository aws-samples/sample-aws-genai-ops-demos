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

// SSM SecureString parameters for secrets (created by setup wizard)
const SECRET_SSM_PARAMS = [
  '/health-analyzer/production/webhook-secret',
  '/health-analyzer/production/slack-webhook-url',
  '/health-analyzer/production/msteams-webhook-url',
  '/health-analyzer/staging/webhook-secret',
  '/health-analyzer/staging/slack-webhook-url',
  '/health-analyzer/staging/msteams-webhook-url',
];

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
  info(`Region: ${region}`);

  const confirmAll = await askYesNo(
    `\n  Are you sure you want to delete ALL resources in ${region} for account ${accountId}?`
  );
  if (!confirmAll) {
    console.log('\n  Cleanup cancelled.');
    rl.close();
    return;
  }

  // ─── Step 1: Destroy CDK Stack ──────────────────────────────────────────
  console.log('\n┌─ Step 1: CDK Stack');
  console.log('└' + '─'.repeat(55));

  const stackName = `HealthEventAnalyzerStack-${region}`;

  try {
    exec(`aws cloudformation describe-stacks --stack-name ${stackName} --region ${region} --no-cli-pager`, true);
    info(`Found stack: ${stackName}`);

    const destroyStack = await askYesNo(`  Destroy CDK stack ${stackName}?`);
    if (destroyStack) {
      info('Destroying CDK stack (this may take a few minutes)...');
      const cdkDir = path.resolve(__dirname, '../infrastructure/cdk');
      try {
        execSync(
          `npx cdk destroy --all --force`,
          { cwd: cdkDir, encoding: 'utf-8', stdio: 'inherit', env: { ...process.env, AWS_REGION: region, AWS_DEFAULT_REGION: region } }
        );
        success(`Stack destroyed: ${stackName}`);
      } catch {
        warn('CDK destroy had issues. You may need to delete the stack manually from CloudFormation console.');
      }
    } else {
      skipped('CDK stack preserved');
    }
  } catch {
    info(`Stack ${stackName} not found — nothing to destroy`);
  }

  // ─── Step 2: Delete DevOps Agent Space ──────────────────────────────────
  console.log('\n┌─ Step 2: DevOps Agent Space');
  console.log('└' + '─'.repeat(55));

  try {
    const spaces = execJson(
      `aws devops-agent list-agent-spaces --region ${region} --no-cli-pager`
    );

    if (spaces.agentSpaces && spaces.agentSpaces.length > 0) {
      const spaceOptions = spaces.agentSpaces.map(
        (s: any) => `${s.name} (${s.agentSpaceId})`
      );
      spaceOptions.push('Skip — do not delete any space');

      const spaceIdx = await askChoice('Which Agent Space do you want to delete?', spaceOptions);

      if (spaceIdx < spaces.agentSpaces.length) {
        const spaceId = spaces.agentSpaces[spaceIdx].agentSpaceId;
        const spaceName = spaces.agentSpaces[spaceIdx].name;

        const confirmSpace = await askYesNo(
          `  ⚠️  Delete Agent Space "${spaceName}" and ALL its associations, webhooks, investigations?`
        );

        if (confirmSpace) {
          // First, disassociate all services
          info('Removing associations...');
          try {
            const associations = execJson(
              `aws devops-agent list-associations --agent-space-id ${spaceId} --region ${region} --no-cli-pager`
            );
            for (const assoc of associations.associations || []) {
              try {
                exec(
                  `aws devops-agent disassociate-service --agent-space-id ${spaceId} --association-id ${assoc.associationId} --region ${region} --no-cli-pager`,
                  true
                );
                success(`Disassociated: ${assoc.serviceId} (${assoc.associationId})`);
              } catch {
                warn(`Failed to disassociate ${assoc.associationId}`);
              }
            }
          } catch {
            // No associations
          }

          // Disable operator app
          info('Disabling operator app...');
          try {
            exec(
              `aws devops-agent disable-operator-app --agent-space-id ${spaceId} --region ${region} --no-cli-pager`,
              true
            );
            success('Operator app disabled');
          } catch {
            // Already disabled or doesn't exist
          }

          // Delete the space
          info('Deleting Agent Space...');
          try {
            exec(
              `aws devops-agent delete-agent-space --agent-space-id ${spaceId} --region ${region} --no-cli-pager`,
              true
            );
            success(`Agent Space deleted: ${spaceName}`);
          } catch (error: any) {
            warn(`Failed to delete Agent Space. You may need to delete it from the console.`);
          }
        } else {
          skipped('Agent Space preserved');
        }
      } else {
        skipped('No Agent Space deleted');
      }
    } else {
      info('No Agent Spaces found in this region');
    }
  } catch {
    info('Could not list Agent Spaces');
  }

  // ─── Step 3: Deregister eventChannel service ────────────────────────────
  console.log('\n┌─ Step 3: Registered Services');
  console.log('└' + '─'.repeat(55));

  try {
    const services = execJson(
      `aws devops-agent list-services --region ${region} --no-cli-pager`
    );

    if (services.services && services.services.length > 0) {
      const deleteServices = await askYesNo('  Delete registered DevOps Agent services (eventChannel, etc.)?');
      if (deleteServices) {
        for (const svc of services.services) {
          try {
            exec(
              `aws devops-agent deregister-service --service-id ${svc.serviceId} --region ${region} --no-cli-pager`,
              true
            );
            success(`Deregistered service: ${svc.serviceType || svc.serviceId}`);
          } catch {
            warn(`Failed to deregister service ${svc.serviceId}`);
          }
        }
      } else {
        skipped('Services preserved');
      }
    } else {
      info('No registered services found');
    }
  } catch {
    info('Could not list services');
  }

  // ─── Step 4: Delete IAM Roles ───────────────────────────────────────────
  console.log('\n┌─ Step 4: IAM Roles');
  console.log('└' + '─'.repeat(55));

  const deleteRoles = await askYesNo('  Delete DevOps Agent IAM roles?');
  if (deleteRoles) {
    await deleteIamRole('DevOpsAgentRole-AgentSpace');
    await deleteIamRole('DevOpsAgentRole-WebappAdmin');
  } else {
    skipped('IAM roles preserved');
  }

  // ─── Step 5: Jira routing config (SSM Parameter Store) ──────────────────
  console.log('\n┌─ Step 5: Jira routing config (SSM Parameter Store)');
  console.log('└' + '─'.repeat(55));

  await deleteJiraSsmParams(region);
  await removeJiraSsmReadGrant();

  // ─── Step 6: Secret SSM Parameters ──────────────────────────────────────
  console.log('\n┌─ Step 6: Secret SSM Parameters');
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

async function removeJiraSsmReadGrant(): Promise<void> {
  const roleName = 'DevOpsAgentRole-AgentSpace';
  const policyName = 'AllowReadHealthAnalyzerJiraSsmParams';
  let exists = false;
  try {
    exec(
      `aws iam get-role-policy --role-name ${roleName} --policy-name ${policyName} --no-cli-pager`,
      true
    );
    exists = true;
  } catch {
    // not present, nothing to clean up
  }
  if (!exists) return;
  try {
    exec(
      `aws iam delete-role-policy --role-name ${roleName} --policy-name ${policyName} --no-cli-pager`,
      true
    );
    success(`Removed inline policy: ${policyName} from ${roleName}`);
  } catch {
    warn(`Failed to remove inline policy ${policyName} from ${roleName} — delete manually if needed.`);
  }
}

async function deleteIamRole(roleName: string): Promise<void> {
  try {
    exec(`aws iam get-role --role-name ${roleName} --no-cli-pager`, true);
  } catch {
    info(`Role ${roleName} does not exist — skipping`);
    return;
  }

  try {
    // Detach managed policies
    const policies = execJson(
      `aws iam list-attached-role-policies --role-name ${roleName} --no-cli-pager`
    );
    for (const policy of policies.AttachedPolicies || []) {
      exec(
        `aws iam detach-role-policy --role-name ${roleName} --policy-arn ${policy.PolicyArn} --no-cli-pager`,
        true
      );
    }

    // Delete inline policies
    const inlinePolicies = execJson(
      `aws iam list-role-policies --role-name ${roleName} --no-cli-pager`
    );
    for (const policyName of inlinePolicies.PolicyNames || []) {
      exec(
        `aws iam delete-role-policy --role-name ${roleName} --policy-name ${policyName} --no-cli-pager`,
        true
      );
    }

    // Delete the role
    exec(`aws iam delete-role --role-name ${roleName} --no-cli-pager`, true);
    success(`Deleted role: ${roleName}`);
  } catch {
    warn(`Failed to delete role ${roleName}. It may have dependencies.`);
  }
}

// ─── Entry Point ────────────────────────────────────────────────────────────

main().catch((error) => {
  console.error('\n❌ Cleanup failed:', error.message);
  rl.close();
});
