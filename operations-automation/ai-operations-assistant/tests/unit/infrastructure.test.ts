import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';

const CDK_LIB_DIR = path.resolve(__dirname, '../../infrastructure/cdk/lib');
const CDK_BIN_DIR = path.resolve(__dirname, '../../infrastructure/cdk/bin');

/**
 * Read all TypeScript files from a directory.
 */
function readTsFiles(dir: string): { name: string; content: string }[] {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith('.ts'))
    .map((f) => ({
      name: f,
      content: fs.readFileSync(path.join(dir, f), 'utf-8'),
    }));
}

describe('Infrastructure Unit Tests', () => {
  const libFiles = readTsFiles(CDK_LIB_DIR);
  const binFiles = readTsFiles(CDK_BIN_DIR);
  const allFiles = [...libFiles, ...binFiles];

  // ---------------------------------------------------------------------------
  // Requirement 11.6: No hardcoded credentials, regions, or API endpoints
  // ---------------------------------------------------------------------------
  describe('No hardcoded credentials, regions, or API endpoints', () => {
    it('should not contain hardcoded AWS access keys', () => {
      for (const file of allFiles) {
        // AWS access key pattern: AKIA followed by 16 alphanumeric chars
        expect(file.content).not.toMatch(/AKIA[0-9A-Z]{16}/);
      }
    });

    it('should not contain hardcoded secret keys', () => {
      for (const file of allFiles) {
        // Typical secret key assignment patterns
        expect(file.content).not.toMatch(/secretAccessKey\s*[:=]\s*['"][A-Za-z0-9/+=]{40}['"]/);
      }
    });

    it('should not contain hardcoded region strings as default values', () => {
      for (const file of allFiles) {
        // Should not have region: 'us-east-1' or region = 'us-east-1' as a default
        // Allow references in ARN patterns, comments, and dynamic constructs
        const lines = file.content.split('\n');
        for (const line of lines) {
          const trimmed = line.trim();
          // Skip comments, ARN patterns, and import statements
          if (
            trimmed.startsWith('//') ||
            trimmed.startsWith('*') ||
            trimmed.startsWith('/*') ||
            trimmed.includes('arn:aws:') ||
            trimmed.includes('region_name=') ||
            trimmed.includes('this.region') ||
            trimmed.includes('cdk.Aws.REGION') ||
            trimmed.includes('Aws.REGION') ||
            trimmed.includes('getRegion') ||
            trimmed.includes('${this.region}') ||
            trimmed.includes('${region}')
          ) {
            continue;
          }
          // Check for hardcoded region assignment
          expect(trimmed).not.toMatch(
            /region\s*[:=]\s*['"]us-east-1['"]/
          );
        }
      }
    });

    it('should not contain hardcoded API endpoint URLs', () => {
      for (const file of allFiles) {
        // Should not have hardcoded https:// API endpoints
        expect(file.content).not.toMatch(
          /https:\/\/[a-z0-9]+\.execute-api\.[a-z0-9-]+\.amazonaws\.com/
        );
      }
    });
  });

  // ---------------------------------------------------------------------------
  // Requirement 10.6: Solution adoption tracking on OrchRuntimeStack only
  // ---------------------------------------------------------------------------
  describe('Solution adoption tracking', () => {
    it('should have solution adoption tracking in app.ts', () => {
      const appFile = binFiles.find((f) => f.name === 'app.ts');
      expect(appFile).toBeDefined();
      expect(appFile!.content).toContain('uksb-do9bhieqqh');
      expect(appFile!.content).toContain('tag:goat,operations-automation');
    });

    it('should apply tracking description only to OrchRuntimeStack', () => {
      const appFile = binFiles.find((f) => f.name === 'app.ts');
      expect(appFile).toBeDefined();

      // Count occurrences of the tracking ID in app.ts
      const trackingMatches = appFile!.content.match(/uksb-do9bhieqqh/g) || [];
      expect(trackingMatches.length).toBe(1);
    });

    it('should not have tracking in any lib stack files', () => {
      for (const file of libFiles) {
        expect(file.content).not.toContain('uksb-do9bhieqqh');
      }
    });
  });

  // ---------------------------------------------------------------------------
  // InfraStacks export via CfnOutput, RuntimeStacks import via Fn.importValue
  // ---------------------------------------------------------------------------
  describe('InfraStack exports and RuntimeStack imports', () => {
    it('InfraStacks should export values via CfnOutput', () => {
      const infraFiles = libFiles.filter((f) => f.name.includes('-infra-stack'));
      expect(infraFiles.length).toBeGreaterThan(0);

      for (const file of infraFiles) {
        // BaseInfraStack handles CfnOutput — check that infra stacks extend it
        // or directly use CfnOutput
        const usesCfnOutput =
          file.content.includes('CfnOutput') ||
          file.content.includes('BaseInfraStack');
        expect(usesCfnOutput).toBe(true);
      }
    });

    it('BaseInfraStack should export RuntimeRoleArn, SourceBucketName, BuildProjectName, BuildProjectArn', () => {
      const baseInfra = libFiles.find((f) => f.name === 'base-infra-stack.ts');
      expect(baseInfra).toBeDefined();
      expect(baseInfra!.content).toContain('RuntimeRoleArn');
      expect(baseInfra!.content).toContain('SourceBucketName');
      expect(baseInfra!.content).toContain('BuildProjectName');
      expect(baseInfra!.content).toContain('BuildProjectArn');
      expect(baseInfra!.content).toContain('exportName');
    });

    it('RuntimeStacks should import values via Fn.importValue', () => {
      const baseRuntime = libFiles.find((f) => f.name === 'base-runtime-stack.ts');
      expect(baseRuntime).toBeDefined();
      expect(baseRuntime!.content).toContain('Fn.importValue');
    });
  });

  // ---------------------------------------------------------------------------
  // RuntimeStacks include a build waiter for CodeBuild polling
  // ---------------------------------------------------------------------------
  describe('Build waiter', () => {
    it('BaseRuntimeStack should include build waiter Lambda functions', () => {
      const baseRuntime = libFiles.find((f) => f.name === 'base-runtime-stack.ts');
      expect(baseRuntime).toBeDefined();
      // The build waiter is implemented via the CDK provider framework so the
      // total wait can exceed the AWS Lambda 15-minute hard limit. Two Lambdas
      // back the provider: an onEvent handler and an isComplete handler.
      expect(baseRuntime!.content).toContain('BuildWaiterOnEvent');
      expect(baseRuntime!.content).toContain('BuildWaiterIsComplete');
      expect(baseRuntime!.content).toContain('BuildWaiterProvider');
      expect(baseRuntime!.content).toContain('BuildWaiter');
    });

    it('build waiter should poll CodeBuild status', () => {
      const baseRuntime = libFiles.find((f) => f.name === 'base-runtime-stack.ts');
      expect(baseRuntime).toBeDefined();
      expect(baseRuntime!.content).toContain('BatchGetBuilds');
      expect(baseRuntime!.content).toContain('codebuild:BatchGetBuilds');
    });

    it('build waiter should have appropriate timeout', () => {
      const baseRuntime = libFiles.find((f) => f.name === 'base-runtime-stack.ts');
      expect(baseRuntime).toBeDefined();
      // Per-Lambda timeout (one poll cycle) and provider framework totalTimeout
      // are both expressed in minutes.
      expect(baseRuntime!.content).toContain('Duration.minutes');
    });

    it('BaseRuntimeStack should expose buildWaitTimeoutMinutes prop with a 14-minute default', () => {
      const baseRuntime = libFiles.find((f) => f.name === 'base-runtime-stack.ts');
      expect(baseRuntime).toBeDefined();
      expect(baseRuntime!.content).toContain('buildWaitTimeoutMinutes');
      expect(baseRuntime!.content).toContain('DEFAULT_BUILD_WAIT_TIMEOUT_MINUTES = 14');
    });

    it('build waiter should drive a cr.Provider with a totalTimeout sourced from the prop', () => {
      const baseRuntime = libFiles.find((f) => f.name === 'base-runtime-stack.ts');
      expect(baseRuntime).toBeDefined();
      expect(baseRuntime!.content).toContain('cr.Provider');
      expect(baseRuntime!.content).toContain('isCompleteHandler');
      expect(baseRuntime!.content).toContain('totalTimeout: cdk.Duration.minutes(buildWaitTimeoutMinutes)');
    });

    it('build waiter should reject buildWaitTimeoutMinutes outside [1, 60]', () => {
      const baseRuntime = libFiles.find((f) => f.name === 'base-runtime-stack.ts');
      expect(baseRuntime).toBeDefined();
      // The provider framework caps totalTimeout at 60 minutes; values outside
      // [1, 60] must throw at synth time.
      expect(baseRuntime!.content).toContain('MAX_BUILD_WAIT_TIMEOUT_MINUTES = 60');
      expect(baseRuntime!.content).toMatch(/buildWaitTimeoutMinutes < 1/);
      expect(baseRuntime!.content).toMatch(/buildWaitTimeoutMinutes > MAX_BUILD_WAIT_TIMEOUT_MINUTES/);
    });

    it('build waiter timeout error should identify the build project name and build identifier (Req 6.14)', () => {
      const baseRuntime = libFiles.find((f) => f.name === 'base-runtime-stack.ts');
      expect(baseRuntime).toBeDefined();
      // The isComplete handler throws an Error including both the project name
      // and the build id when the elapsed time exceeds the configured budget.
      expect(baseRuntime!.content).toContain('BuildProjectName');
      expect(baseRuntime!.content).toMatch(/budget for project[\s\S]*BuildId|build id/);
    });

    it('NetworkRuntimeStack should pass buildWaitTimeoutMinutes=30 (Req 6.13)', () => {
      const networkRuntime = libFiles.find((f) => f.name === 'network-runtime-stack.ts');
      expect(networkRuntime).toBeDefined();
      expect(networkRuntime!.content).toMatch(/buildWaitTimeoutMinutes\s*:\s*30/);
    });
  });

  // ---------------------------------------------------------------------------
  // FrontendStack exists and exports WebsiteUrl
  // ---------------------------------------------------------------------------
  describe('FrontendStack', () => {
    it('should exist', () => {
      const frontendStack = libFiles.find((f) => f.name === 'frontend-stack.ts');
      expect(frontendStack).toBeDefined();
    });

    it('should export WebsiteUrl', () => {
      const frontendStack = libFiles.find((f) => f.name === 'frontend-stack.ts');
      expect(frontendStack).toBeDefined();
      expect(frontendStack!.content).toContain('WebsiteUrl');
    });

    it('should use CloudFront with OAC', () => {
      const frontendStack = libFiles.find((f) => f.name === 'frontend-stack.ts');
      expect(frontendStack).toBeDefined();
      expect(frontendStack!.content).toContain('S3OriginAccessControl');
      expect(frontendStack!.content).toContain('Distribution');
    });
  });

  // ---------------------------------------------------------------------------
  // CDK app entry point structure
  // ---------------------------------------------------------------------------
  describe('CDK app entry point', () => {
    it('app.ts should exist in bin/', () => {
      const appFile = binFiles.find((f) => f.name === 'app.ts');
      expect(appFile).toBeDefined();
    });

    it('app.ts should import getRegion from shared utils', () => {
      const appFile = binFiles.find((f) => f.name === 'app.ts');
      expect(appFile).toBeDefined();
      expect(appFile!.content).toContain('getRegion');
      expect(appFile!.content).toContain('shared/utils/aws-utils');
    });

    it('app.ts should use region-suffixed stack IDs', () => {
      const appFile = binFiles.find((f) => f.name === 'app.ts');
      expect(appFile).toBeDefined();
      // All stack instantiations should use ${region} suffix
      expect(appFile!.content).toContain('`GOATAuth-${region}`');
      expect(appFile!.content).toContain('`GOATData-${region}`');
      expect(appFile!.content).toContain('`GOATOrchRuntime-${region}`');
      expect(appFile!.content).toContain('`GOATFrontend-${region}`');
    });

    // -------------------------------------------------------------------------
    // Network Agent stack wiring (Task 30 / Reqs 7.x, 9.7, 9.8, 10.x, 15.x)
    // -------------------------------------------------------------------------
    it('app.ts should wire all three Network stacks with region-suffixed IDs', () => {
      const appFile = binFiles.find((f) => f.name === 'app.ts');
      expect(appFile).toBeDefined();
      expect(appFile!.content).toContain('`GOATNetworkData-${region}`');
      expect(appFile!.content).toContain('`GOATNetworkInfra-${region}`');
      expect(appFile!.content).toContain('`GOATNetworkRuntime-${region}`');
    });

    it('app.ts should instantiate NetworkDataStack only when GOATSharedDataBucketName is absent', () => {
      const appFile = binFiles.find((f) => f.name === 'app.ts');
      expect(appFile).toBeDefined();
      // The conditional lookup uses a CDK context flag set by the deploy
      // scripts after they perform the CFN export lookup out-of-band.
      expect(appFile!.content).toContain("tryGetContext('goatSharedDataBucketName')");
      // NetworkDataStack must only be created when the context value is
      // empty/undefined.
      expect(appFile!.content).toMatch(/sharedDataBucketName === undefined/);
      expect(appFile!.content).toContain('new NetworkDataStack(app, `GOATNetworkData-${region}`');
    });

    it('app.ts should pass the resolved bucket name into NetworkInfraStack', () => {
      const appFile = binFiles.find((f) => f.name === 'app.ts');
      expect(appFile).toBeDefined();
      expect(appFile!.content).toMatch(/networkDataBucketName:\s*resolvedNetworkDataBucketName/);
    });

    it('app.ts should make NetworkRuntimeStack depend on NetworkInfraStack', () => {
      const appFile = binFiles.find((f) => f.name === 'app.ts');
      expect(appFile).toBeDefined();
      expect(appFile!.content).toMatch(/networkRuntime\.addDependency\(networkInfra\)/);
    });

    it('app.ts should pass the Network Agent runtime ARN to OrchRuntimeStack subAgentArns', () => {
      const appFile = binFiles.find((f) => f.name === 'app.ts');
      expect(appFile).toBeDefined();
      // The new `network` key in subAgentArns must map to the Network Agent
      // runtime ARN exported by NetworkRuntimeStack.
      expect(appFile!.content).toMatch(/network:\s*networkRuntime\.agentRuntimeArn/);
    });

    it('app.ts should make OrchRuntimeStack depend on NetworkRuntimeStack', () => {
      const appFile = binFiles.find((f) => f.name === 'app.ts');
      expect(appFile).toBeDefined();
      expect(appFile!.content).toMatch(/orchRuntime\.addDependency\(networkRuntime\)/);
    });

    it('OrchRuntimeStack should expose NETWORK_AGENT_ARN environment variable', () => {
      const orchRuntime = libFiles.find((f) => f.name === 'orch-runtime-stack.ts');
      expect(orchRuntime).toBeDefined();
      expect(orchRuntime!.content).toMatch(/NETWORK_AGENT_ARN:\s*props\.subAgentArns\.network/);
      // The interface must declare the new key.
      expect(orchRuntime!.content).toMatch(/network:\s*string/);
    });
  });
});
