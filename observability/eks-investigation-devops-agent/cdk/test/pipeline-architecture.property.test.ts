/**
 * Feature: cfn-to-cdk-migration, Property 2: Architecture-dependent configuration mapping (CodeBuild)
 *
 * Generate random architecture values from {arm64, amd64}, synthesize pipeline
 * stack, verify CodeBuild environment type is ARM_CONTAINER or LINUX_CONTAINER
 * respectively.
 *
 * Validates: Requirements 5.3
 */
import * as fc from 'fast-check';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { PipelineStack } from '../lib/pipeline-stack';

function synthesizePipelineStack(architecture: string): Template {
  const app = new cdk.App();
  const stack = new PipelineStack(app, 'TestPipelineStack', {
    env: { region: 'us-east-1', account: '123456789012' },
    environment: 'dev',
    projectName: 'devops-agent-eks',
    eksNodeArchitecture: architecture,
  });
  return Template.fromStack(stack);
}

describe('Property 2: Architecture-dependent configuration mapping (CodeBuild)', () => {
  it('CodeBuild environment type matches the architecture parameter', () => {
    const archArb = fc.constantFrom('arm64', 'amd64');

    fc.assert(
      fc.property(archArb, (architecture) => {
        const template = synthesizePipelineStack(architecture);
        const expectedType = architecture === 'arm64' ? 'ARM_CONTAINER' : 'LINUX_CONTAINER';

        const resources = template.toJSON().Resources;
        const codeBuildProjects = Object.values(resources as Record<string, any>).filter(
          (r: any) => r.Type === 'AWS::CodeBuild::Project',
        );

        expect(codeBuildProjects).toHaveLength(3);
        for (const project of codeBuildProjects) {
          expect((project as any).Properties.Environment.Type).toBe(expectedType);
        }
      }),
      { numRuns: 100 },
    );
  });

  it('CodeBuild image matches the architecture parameter', () => {
    const archArb = fc.constantFrom('arm64', 'amd64');

    fc.assert(
      fc.property(archArb, (architecture) => {
        const template = synthesizePipelineStack(architecture);
        const expectedImage = architecture === 'arm64'
          ? 'aws/codebuild/amazonlinux-aarch64-standard:3.0'
          : 'aws/codebuild/amazonlinux-x86_64-standard:5.0';

        const resources = template.toJSON().Resources;
        const codeBuildProjects = Object.values(resources as Record<string, any>).filter(
          (r: any) => r.Type === 'AWS::CodeBuild::Project',
        );

        for (const project of codeBuildProjects) {
          expect((project as any).Properties.Environment.Image).toBe(expectedImage);
        }
      }),
      { numRuns: 100 },
    );
  });
});