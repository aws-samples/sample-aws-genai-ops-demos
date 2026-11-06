/**
 * Feature: cfn-to-cdk-migration, Property 9: Deployment script region-suffixed stack names.
 *
 * Infrastructure stacks use AWS_REGION; the Agent Space stack uses
 * DEVOPS_AGENT_REGION because it can live elsewhere. Every describe-stacks
 * invocation must resolve to one of those region-suffixed names.
 */
import * as fc from 'fast-check';
import * as fs from 'fs';
import * as path from 'path';

function extractStackNames(content: string): string[] {
  const pattern = /--stack-name\s+["']?([^"'\s\\`]+)["']?/g;
  const names: string[] = [];
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(content)) !== null) names.push(match[1]);
  return names;
}

function isRegionScopedName(name: string): boolean {
  return name.includes('$AWS_REGION')
    || name.includes('$DEVOPS_AGENT_REGION')
    || name === '$AGENT_SPACE_STACK_NAME'
    || name === '$AgentSpaceStackName';
}

function resolveName(name: string, deployRegion: string, agentRegion: string): string {
  if (name === '$AGENT_SPACE_STACK_NAME' || name === '$AgentSpaceStackName') {
    return `DevOpsAgentEksAgentSpace-${agentRegion}`;
  }
  return name
    .replace(/\$AWS_REGION/g, deployRegion)
    .replace(/\$DEVOPS_AGENT_REGION/g, agentRegion);
}

describe('Property 9: Deployment script region-suffixed stack names', () => {
  const bashContent = fs.readFileSync(path.resolve(__dirname, '../../deploy-all.sh'), 'utf-8');
  const psContent = fs.readFileSync(path.resolve(__dirname, '../../deploy-all.ps1'), 'utf-8');
  const bashStackNames = extractStackNames(bashContent);
  const psStackNames = extractStackNames(psContent);

  it('both deploy scripts describe at least one stack', () => {
    expect(bashStackNames.length).toBeGreaterThan(0);
    expect(psStackNames.length).toBeGreaterThan(0);
  });

  it('every describe-stacks name is scoped to deploy or Agent Space region', () => {
    for (const name of [...bashStackNames, ...psStackNames]) {
      expect(isRegionScopedName(name)).toBe(true);
    }
  });

  it('resolves valid suffixes for same-region and split-region deployments', () => {
    const regionArb = fc
      .tuple(
        fc.constantFrom('us', 'eu', 'ap', 'sa', 'ca', 'me', 'af'),
        fc.constantFrom('east', 'west', 'north', 'south', 'central', 'southeast', 'northeast'),
        fc.integer({ min: 1, max: 4 }),
      )
      .map(([prefix, direction, num]) => `${prefix}-${direction}-${num}`);

    fc.assert(
      fc.property(regionArb, regionArb, (deployRegion, agentRegion) => {
        for (const name of [...bashStackNames, ...psStackNames]) {
          const resolved = resolveName(name, deployRegion, agentRegion);
          expect(resolved.endsWith(`-${deployRegion}`) || resolved.endsWith(`-${agentRegion}`)).toBe(true);
          expect(resolved).not.toContain('$');
        }
      }),
      { numRuns: 100 },
    );
  });
});
