/**
 * Feature: cfn-to-cdk-migration, Property 7: Environment-dependent log retention
 *
 * Generate random environment values from {dev, staging, prod}, synthesize
 * auth stack, verify log retention is 365 days for prod and 30 days otherwise.
 *
 * Validates: Requirements 6.4
 */
import * as fc from 'fast-check';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { AuthStack } from '../lib/auth-stack';

// ---------------------------------------------------------------------------
// Helper: synthesize AuthStack with a given environment
// ---------------------------------------------------------------------------
function synthesizeAuthStack(environment: string): Template {
  const app = new cdk.App();

  const stack = new AuthStack(app, 'TestAuthStack', {
    env: { region: 'us-east-1', account: '123456789012' },
    environment,
    projectName: 'devops-agent-eks',
  });

  return Template.fromStack(stack);
}

// ---------------------------------------------------------------------------
// Property test
// ---------------------------------------------------------------------------
describe('Property 7: Environment-dependent log retention', () => {
  it('Cognito log group retention is 365 days for prod and 30 days otherwise', () => {
    const envArb = fc.constantFrom('dev', 'staging', 'prod');

    fc.assert(
      fc.property(envArb, (environment) => {
        const template = synthesizeAuthStack(environment);
        const expectedRetention = environment === 'prod' ? 365 : 30;

        template.hasResourceProperties('AWS::Logs::LogGroup', {
          RetentionInDays: expectedRetention,
        });
      }),
      { numRuns: 100 },
    );
  });

  it('Cognito log group name includes environment', () => {
    const envArb = fc.constantFrom('dev', 'staging', 'prod');

    fc.assert(
      fc.property(envArb, (environment) => {
        const template = synthesizeAuthStack(environment);

        template.hasResourceProperties('AWS::Logs::LogGroup', {
          LogGroupName: `/aws/cognito/devops-agent-eks-${environment}`,
        });
      }),
      { numRuns: 100 },
    );
  });
});
