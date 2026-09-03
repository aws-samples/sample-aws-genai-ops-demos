/**
 * Tests for the setup wizard production readiness requirements.
 *
 * Validates:
 * - Region prompt always occurs before deployment steps (Req 16.2)
 * - --require-approval broadening enforced for production (Req 15.3, 15.6)
 * - No process.exit(1) or unhandled exceptions (Req 16.4)
 * - No set -e or equivalent (Req 16.5)
 * - Graceful error handling with retry/skip/abort (Req 16.3)
 * - Completion summary with succeeded/failed steps (Req 16.6)
 */

import * as fs from 'fs';
import * as path from 'path';

const WIZARD_PATH = path.resolve(__dirname, '../scripts/setup-wizard.ts');

/**
 * Extracts a top-level function's source text from the wizard.
 *
 * Slices from the function declaration to whichever comes first: the next
 * top-level function declaration, the trailing `main().catch(...)` bootstrap, or
 * end of file. Previously these assertions used a fixed 4000-character window,
 * which silently truncated the body whenever the function grew — causing
 * unrelated assertions to fail on content that was still present.
 */
function extractFunctionSource(source: string, declaration: string): string {
  const startIdx = source.indexOf(declaration);
  if (startIdx === -1) return '';

  const searchFrom = startIdx + declaration.length;
  const boundaries = [
    source.indexOf('\nasync function ', searchFrom),
    source.indexOf('\nfunction ', searchFrom),
    source.indexOf('\nmain().catch(', searchFrom),
  ].filter((idx) => idx !== -1);

  const endIdx = boundaries.length > 0 ? Math.min(...boundaries) : source.length;
  return source.slice(startIdx, endIdx);
}

describe('setup-wizard production readiness', () => {
  let wizardSource: string;

  beforeAll(() => {
    wizardSource = fs.readFileSync(WIZARD_PATH, 'utf-8');
  });

  describe('Requirement 16.2: Region resolved before deployment', () => {
    it('resolves the region as the first step (Step 0) in the main flow', () => {
      // Region is established as Step 0, before any prerequisites or deployment.
      // Per review feedback the wizard no longer prompts from a hardcoded region
      // shortlist; it resolves the region from the environment / AWS CLI config
      // and lets the shared prerequisites check verify AgentCore availability.
      const regionStepMatch = wizardSource.match(
        /step\(0,\s*['"`]Resolve Target AWS Region['"`]\)/
      );
      expect(regionStepMatch).not.toBeNull();
    });

    it('does not hardcode a region shortlist', () => {
      // The old SUPPORTED_REGIONS picker rotted and was inaccurate; it must not
      // return. Availability is proven at runtime by the shared prerequisites
      // check (check-prerequisites --required-service agentcore).
      expect(wizardSource).not.toContain('SUPPORTED_REGIONS');
      expect(wizardSource).toContain('function resolveRegion');
    });

    it('region resolution occurs before CDK deployment step in source order', () => {
      const regionPromptIdx = wizardSource.indexOf("'Resolve Target AWS Region'");
      const cdkDeployIdx = wizardSource.indexOf("'CDK Deployment'");
      expect(regionPromptIdx).toBeGreaterThan(-1);
      expect(cdkDeployIdx).toBeGreaterThan(-1);
      expect(regionPromptIdx).toBeLessThan(cdkDeployIdx);
    });

    it('region resolution occurs before any DevOps Agent API calls', () => {
      // In the main flow, region is resolved first (Step 0), prerequisites are Step 1
      const regionStepIdx = wizardSource.indexOf("step(0, 'Resolve Target AWS Region')");
      const prerequisitesIdx = wizardSource.indexOf("step(1, 'Checking prerequisites')");
      expect(regionStepIdx).toBeGreaterThan(-1);
      expect(prerequisitesIdx).toBeGreaterThan(-1);
      expect(regionStepIdx).toBeLessThan(prerequisitesIdx);
    });
  });

  describe('Requirement 15.3, 15.6: Enforce --require-approval broadening', () => {
    it('uses --require-approval broadening in the deploy command', () => {
      expect(wizardSource).toContain("'--require-approval=broadening'");
    });

    it('does not use --require-approval never anywhere', () => {
      expect(wizardSource).not.toContain('--require-approval never');
    });
  });

  describe('Requirement 16.4: No process.exit(1) or unhandled exceptions', () => {
    it('does not contain process.exit(1)', () => {
      expect(wizardSource).not.toMatch(/process\.exit\(1\)/);
    });

    it('does not contain process.exit(2)', () => {
      expect(wizardSource).not.toMatch(/process\.exit\(2\)/);
    });

    it('does not contain any process.exit calls', () => {
      expect(wizardSource).not.toMatch(/process\.exit\(/);
    });

    it('has a top-level .catch() handler on main()', () => {
      // The entry point wraps main() in a .catch() to handle any unhandled rejections
      expect(wizardSource).toMatch(/main\(\)\.catch\(/);
    });

    it('top-level catch does not use process.exit', () => {
      // Extract the catch handler block
      const catchIdx = wizardSource.lastIndexOf('main().catch(');
      expect(catchIdx).toBeGreaterThan(-1);
      const afterCatch = wizardSource.slice(catchIdx);
      expect(afterCatch).not.toContain('process.exit');
    });
  });

  describe('Requirement 16.5: No set -e or equivalent', () => {
    it('does not contain set -e', () => {
      expect(wizardSource).not.toContain('set -e');
    });

    it('does not contain set -o errexit', () => {
      expect(wizardSource).not.toContain('set -o errexit');
    });

    it('does not contain set -o pipefail as a termination mechanism', () => {
      // set -o pipefail alone is not equivalent to set -e, but we check
      // it's not combined with set -e style behavior
      const hasPipefail = wizardSource.includes('set -o pipefail');
      if (hasPipefail) {
        // If pipefail is present, make sure it's not combined with errexit
        expect(wizardSource).not.toContain('set -eo pipefail');
      }
    });
  });

  describe('Requirement 16.3: Graceful error handling with retry/skip/abort', () => {
    it('defines an askRetrySkipAbort function', () => {
      expect(wizardSource).toContain('async function askRetrySkipAbort');
    });

    it('askRetrySkipAbort offers retry, skip, and abort options', () => {
      expect(wizardSource).toContain('Retry this step');
      expect(wizardSource).toContain('Skip this step and continue');
      expect(wizardSource).toContain('Abort the wizard');
    });

    it('defines a runStep helper that wraps steps with error handling', () => {
      expect(wizardSource).toContain('async function runStep');
    });

    it('runStep catches errors and invokes askRetrySkipAbort', () => {
      // Extract the runStep function body
      const runStepIdx = wizardSource.indexOf('async function runStep');
      expect(runStepIdx).toBeGreaterThan(-1);
      const bodyAfter = wizardSource.slice(runStepIdx, runStepIdx + 800);
      expect(bodyAfter).toContain('catch');
      expect(bodyAfter).toContain('askRetrySkipAbort');
    });

    it('uses runStep for major wizard steps in main flow', () => {
      // The main flow should use runStep for key steps
      expect(wizardSource).toContain("runStep('Check prerequisites'");
      expect(wizardSource).toContain("runStep('DevOps Agent Space'");
      expect(wizardSource).toContain("runStep('IAM Roles'");
      expect(wizardSource).toContain("runStep('Account Association'");
      expect(wizardSource).toContain("runStep('Webhook Configuration'");
      expect(wizardSource).toContain("runStep('CDK Deployment'");
    });
  });

  describe('Requirement 16.6: Completion summary', () => {
    it('defines a displayCompletionSummary function', () => {
      expect(wizardSource).toContain('function displayCompletionSummary');
    });

    it('tracks step results with succeeded/failed/skipped status', () => {
      expect(wizardSource).toContain("type StepStatus = 'succeeded' | 'failed' | 'skipped'");
    });

    it('displayCompletionSummary shows succeeded steps', () => {
      const summaryIdx = wizardSource.indexOf('function displayCompletionSummary');
      expect(summaryIdx).toBeGreaterThan(-1);
      const body = wizardSource.slice(summaryIdx, summaryIdx + 1500);
      expect(body).toContain('Succeeded');
    });

    it('displayCompletionSummary shows failed steps', () => {
      const summaryIdx = wizardSource.indexOf('function displayCompletionSummary');
      expect(summaryIdx).toBeGreaterThan(-1);
      const body = wizardSource.slice(summaryIdx, summaryIdx + 1500);
      expect(body).toContain('Failed');
    });

    it('displayCompletionSummary shows skipped steps', () => {
      const summaryIdx = wizardSource.indexOf('function displayCompletionSummary');
      expect(summaryIdx).toBeGreaterThan(-1);
      const body = wizardSource.slice(summaryIdx, summaryIdx + 1500);
      expect(body).toContain('Skipped');
    });

    it('displayCompletionSummary provides actionable guidance on failures', () => {
      const summaryIdx = wizardSource.indexOf('function displayCompletionSummary');
      expect(summaryIdx).toBeGreaterThan(-1);
      const body = wizardSource.slice(summaryIdx, summaryIdx + 1500);
      expect(body).toContain('Guidance');
      expect(body).toContain('Re-run the wizard');
    });

    it('calls displayCompletionSummary in the main flow', () => {
      // The main function should call displayCompletionSummary
      const mainIdx = wizardSource.indexOf('async function main()');
      expect(mainIdx).toBeGreaterThan(-1);
      const mainBody = wizardSource.slice(mainIdx);
      expect(mainBody).toContain('displayCompletionSummary()');
    });

    it('calls displayCompletionSummary in the top-level catch handler', () => {
      const catchIdx = wizardSource.lastIndexOf('main().catch(');
      expect(catchIdx).toBeGreaterThan(-1);
      const afterCatch = wizardSource.slice(catchIdx);
      expect(afterCatch).toContain('displayCompletionSummary');
    });
  });

  describe('Requirement 16.1: Single canonical entry point', () => {
    it('does not reference deploy-all.sh in the wizard source', () => {
      expect(wizardSource).not.toContain('deploy-all.sh');
    });

    it('does not reference deploy-all.ps1 in the wizard source', () => {
      expect(wizardSource).not.toContain('deploy-all.ps1');
    });
  });

  describe('deployCdk function resilience', () => {
    it('deployCdk throws errors instead of calling process.exit', () => {
      // Find the deployCdk function
      // Get the function body (until next top-level function or end of file)
      const fnBody = extractFunctionSource(wizardSource, 'async function deployCdk');
      expect(fnBody).not.toBe('');
      expect(fnBody).not.toContain('process.exit');
      expect(fnBody).toContain("throw new Error");
    });

    it('deployCdk uses --require-approval broadening', () => {
      const fnBody = extractFunctionSource(wizardSource, 'async function deployCdk');
      expect(fnBody).not.toBe('');
      expect(fnBody).toContain('--require-approval=broadening');
      expect(fnBody).not.toContain('--require-approval never');
    });

    it('deployCdk stores secrets in SSM SecureString before deploying', () => {
      const fnBody = extractFunctionSource(wizardSource, 'async function deployCdk');
      expect(fnBody).not.toBe('');
      expect(fnBody).toContain('putSsmSecureString');
      expect(fnBody).toContain('webhook-secret');
      expect(fnBody).toContain('slack-webhook-url');
      expect(fnBody).toContain('msteams-webhook-url');
    });

    it('deployCdk does not pass secrets as CloudFormation parameters', () => {
      const fnBody = extractFunctionSource(wizardSource, 'async function deployCdk');
      expect(fnBody).not.toBe('');
      expect(fnBody).not.toContain('--parameters DevOpsAgentWebhookSecret');
      expect(fnBody).not.toContain('--parameters SlackWebhookUrl');
      expect(fnBody).not.toContain('--parameters MsTeamsWebhookUrl');
    });
  });
});
