/**
 * Feature: cfn-to-cdk-migration, Property 8: Frontend API origin conditionality
 *
 * Generate random boolean for API endpoint presence, synthesize frontend stack,
 * verify CloudFront distribution includes API origin and `/api/*` behavior
 * if and only if endpoint is provided.
 *
 * Validates: Requirements 7.3
 */
import * as fc from 'fast-check';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { FrontendStack } from '../lib/frontend-stack';

// ---------------------------------------------------------------------------
// Helper: synthesize FrontendStack with optional API endpoint
// ---------------------------------------------------------------------------
function synthesizeFrontendStack(apiEndpoint?: string): Template {
  const app = new cdk.App();

  const stack = new FrontendStack(app, 'TestFrontendStack', {
    env: { region: 'us-east-1', account: '123456789012' },
    environment: 'dev',
    projectName: 'devops-agent-eks',
    ...(apiEndpoint ? { apiGatewayEndpoint: apiEndpoint } : {}),
  });

  return Template.fromStack(stack);
}

// ---------------------------------------------------------------------------
// Helper: generate a plausible NLB-style domain name
// ---------------------------------------------------------------------------
const nlbDomainArb = fc
  .tuple(
    fc.string({ minLength: 4, maxLength: 12 }),
    fc.constantFrom('us-east-1', 'eu-west-1', 'ap-southeast-2'),
  )
  .map(([hash, region]) => `nlb-${hash.replace(/[^a-z0-9]/g, 'x')}.elb.${region}.amazonaws.com`);

// ---------------------------------------------------------------------------
// Property tests
// ---------------------------------------------------------------------------
describe('Property 8: Frontend API origin conditionality', () => {
  it('distribution has API origin and /api/* behavior when endpoint is provided', () => {
    fc.assert(
      fc.property(nlbDomainArb, (endpoint) => {
        const template = synthesizeFrontendStack(endpoint);

        // Should have at least 2 origins (S3 + API)
        template.hasResourceProperties('AWS::CloudFront::Distribution', {
          DistributionConfig: {
            Origins: Match.arrayWith([
              Match.objectLike({
                CustomOriginConfig: Match.objectLike({
                  OriginProtocolPolicy: 'http-only',
                }),
              }),
            ]),
          },
        });

        // Should have /api/* cache behavior
        template.hasResourceProperties('AWS::CloudFront::Distribution', {
          DistributionConfig: {
            CacheBehaviors: Match.arrayWith([
              Match.objectLike({
                PathPattern: '/api/*',
              }),
            ]),
          },
        });
      }),
      { numRuns: 100 },
    );
  });

  it('distribution has NO API origin when endpoint is absent', () => {
    const template = synthesizeFrontendStack(undefined);

    // Get the distribution and verify no custom origin exists
    const distributions = template.findResources('AWS::CloudFront::Distribution');
    const distKeys = Object.keys(distributions);
    expect(distKeys.length).toBe(1);

    const distConfig = distributions[distKeys[0]].Properties.DistributionConfig;

    // No origin should have CustomOriginConfig
    const hasCustomOrigin = distConfig.Origins.some(
      (o: Record<string, unknown>) => 'CustomOriginConfig' in o,
    );
    expect(hasCustomOrigin).toBe(false);

    // No /api/* cache behavior should exist
    const cacheBehaviors = distConfig.CacheBehaviors ?? [];
    const hasApiBehavior = cacheBehaviors.some(
      (b: Record<string, unknown>) => b.PathPattern === '/api/*',
    );
    expect(hasApiBehavior).toBe(false);
  });

  it('SPA error responses are always present regardless of API endpoint', () => {
    const withApiArb = fc.boolean();

    fc.assert(
      fc.property(withApiArb, (hasApi) => {
        const template = hasApi
          ? synthesizeFrontendStack('my-nlb.elb.us-east-1.amazonaws.com')
          : synthesizeFrontendStack(undefined);

        // Both 403 and 404 should map to /index.html
        template.hasResourceProperties('AWS::CloudFront::Distribution', {
          DistributionConfig: {
            CustomErrorResponses: Match.arrayWith([
              Match.objectLike({ ErrorCode: 403, ResponsePagePath: '/index.html', ResponseCode: 200 }),
              Match.objectLike({ ErrorCode: 404, ResponsePagePath: '/index.html', ResponseCode: 200 }),
            ]),
          },
        });
      }),
      { numRuns: 100 },
    );
  });
});
