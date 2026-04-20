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
  // RuntimeStacks include BuildWaiterFunction Lambda for CodeBuild polling
  // ---------------------------------------------------------------------------
  describe('BuildWaiterFunction Lambda', () => {
    it('BaseRuntimeStack should include BuildWaiterFunction Lambda', () => {
      const baseRuntime = libFiles.find((f) => f.name === 'base-runtime-stack.ts');
      expect(baseRuntime).toBeDefined();
      expect(baseRuntime!.content).toContain('BuildWaiterFunction');
    });

    it('BuildWaiterFunction should poll CodeBuild status', () => {
      const baseRuntime = libFiles.find((f) => f.name === 'base-runtime-stack.ts');
      expect(baseRuntime).toBeDefined();
      expect(baseRuntime!.content).toContain('BatchGetBuilds');
      expect(baseRuntime!.content).toContain('codebuild:BatchGetBuilds');
    });

    it('BuildWaiterFunction should have appropriate timeout', () => {
      const baseRuntime = libFiles.find((f) => f.name === 'base-runtime-stack.ts');
      expect(baseRuntime).toBeDefined();
      // Should have a timeout of at least 10 minutes
      expect(baseRuntime!.content).toContain('Duration.minutes');
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
  });
});
