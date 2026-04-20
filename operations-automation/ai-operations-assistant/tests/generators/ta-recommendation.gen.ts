/**
 * fast-check arbitraries for Trusted Advisor recommendation objects.
 * Validates: Requirements 5.3
 */
import fc from 'fast-check';

/** Trusted Advisor pillar categories */
export const arbPillar = fc.constantFrom<
  'cost_optimizing' | 'security' | 'performance' | 'fault_tolerance' | 'service_limits'
>('cost_optimizing', 'security', 'performance', 'fault_tolerance', 'service_limits');

/** TA check status */
export const arbCheckStatus = fc.constantFrom<'ok' | 'warning' | 'error'>('ok', 'warning', 'error');

/** A single Trusted Advisor check descriptor */
export const arbTACheck = fc.record({
  id: fc.stringMatching(/^[A-Za-z0-9]{12}$/),
  name: fc.string({ minLength: 5, maxLength: 80 }),
  description: fc.string({ minLength: 10, maxLength: 300 }),
  category: arbPillar,
  metadata: fc.array(fc.string({ minLength: 1, maxLength: 30 }), { minLength: 0, maxLength: 6 }),
});

/** A single Trusted Advisor recommendation */
export const arbTARecommendation = fc.record({
  checkId: fc.stringMatching(/^[A-Za-z0-9]{12}$/),
  name: fc.string({ minLength: 5, maxLength: 80 }),
  pillar: arbPillar,
  status: arbCheckStatus,
  resourcesSummary: fc.record({
    resourcesProcessed: fc.integer({ min: 0, max: 1000 }),
    resourcesFlagged: fc.integer({ min: 0, max: 500 }),
    resourcesIgnored: fc.integer({ min: 0, max: 100 }),
    resourcesSuppressed: fc.integer({ min: 0, max: 50 }),
  }),
  estimatedMonthlySavings: fc.option(
    fc.double({ min: 0, max: 50_000, noNaN: true }).map((n) => Math.round(n * 100) / 100),
    { nil: undefined },
  ),
  description: fc.string({ minLength: 10, maxLength: 300 }),
});

/** TAChecksParams matching the interface in types.ts */
export const arbTAChecksParams = fc.record({
  pillar: fc.option(arbPillar, { nil: undefined }),
  language: fc.option(fc.constantFrom('en', 'ja', 'fr', 'zh'), { nil: undefined }),
});

/** TACheckResultParams */
export const arbTACheckResultParams = fc.record({
  checkId: fc.stringMatching(/^[A-Za-z0-9]{12}$/),
});

/** TARecommendationsParams */
export const arbTARecommendationsParams = fc.record({
  pillar: fc.option(arbPillar.map(String), { nil: undefined }),
  status: fc.option(arbCheckStatus, { nil: undefined }),
});

/** A formatted TA response (what the TA Agent returns) */
export const arbTAResponse = fc.record({
  success: fc.constant(true),
  domain: fc.constant('trusted_advisor'),
  data: fc.record({
    recommendations: fc.array(arbTARecommendation, { minLength: 0, maxLength: 10 }),
  }),
  formattedText: fc.string({ minLength: 1, maxLength: 500 }),
  metadata: fc.record({
    sourceApi: fc.constantFrom(
      'trustedadvisor:DescribeTrustedAdvisorChecks',
      'trustedadvisor:DescribeTrustedAdvisorCheckResult',
      'trustedadvisor:ListRecommendations',
    ),
    queryTimestamp: fc.date({ min: new Date('2024-01-01') }).map((d) => d.toISOString()),
    dataFreshness: fc.constantFrom('real-time', '24-hours'),
  }),
});
