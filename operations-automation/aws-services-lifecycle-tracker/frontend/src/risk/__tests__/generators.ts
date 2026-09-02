/**
 * fast-check generators (arbitraries) for the Operational Risk Assessment domain.
 *
 * These generators produce valid domain objects for property-based testing,
 * allowing controlled phase targeting via date manipulation.
 *
 * **Validates: Requirements 7.2**
 */

import * as fc from 'fast-check';
import type { DeprecationItem } from '../../api';
import type {
  LifecyclePhase,
  RiskLevel,
  RiskMatrix,
  RiskAssessment,
  Impact,
  Recommendation,
  ComplianceImpact,
  ExtendedSupportPricing,
  PricingTier,
} from '../types';

// --- Constants ---

const LIFECYCLE_PHASES: LifecyclePhase[] = [
  'extended_support',
  'end_of_standard_support',
  'end_of_life',
  'block_create_update',
];

const RISK_LEVELS: RiskLevel[] = ['critique', 'élevé', 'moyen', 'faible'];

const DEPRECATION_STATUSES: DeprecationItem['status'][] = [
  'deprecated',
  'extended_support',
  'end_of_life',
];

const SERVICE_NAMES = [
  'Amazon RDS',
  'Amazon ElastiCache',
  'Amazon EKS',
  'Amazon OpenSearch',
  'Amazon MemoryDB',
  'AWS Lambda',
  'Amazon DynamoDB',
  'Amazon Aurora',
];

const IMPACT_DIMENSIONS: Impact['dimension'][] = [
  'cost',
  'security',
  'compliance',
  'availability',
];

const SEVERITIES: Impact['severity'][] = ['critical', 'warning', 'info'];

const EFFORT_LEVELS: Recommendation['effortLevel'][] = ['low', 'medium', 'high'];

const PRIORITIES: Recommendation['priority'][] = ['immediate', 'planned', 'monitor'];

const COMPLIANCE_FRAMEWORKS: ComplianceImpact['framework'][] = [
  'SOC2',
  'PCI-DSS',
  'HIPAA',
];

// --- Helper: Date Manipulation ---

/**
 * Reference date used for phase-targeted generation.
 * Using a fixed date makes tests deterministic relative to "now".
 */
const REFERENCE_NOW = new Date('2025-01-15T00:00:00Z');

/** Format a Date as ISO string (YYYY-MM-DD) */
function toISODateString(d: Date): string {
  return d.toISOString().split('T')[0];
}

/** Create a date offset from reference by the given number of days. */
function offsetDays(reference: Date, days: number): Date {
  const d = new Date(reference);
  d.setDate(d.getDate() + days);
  return d;
}

// --- Generator: DeprecationItem ---

/**
 * Generates a valid DeprecationItem with lifecycle phase controlled via date manipulation.
 *
 * When `phase` is specified, the generator sets dates in `service_specific` so that
 * `determineLifecyclePhase` will resolve to the requested phase relative to REFERENCE_NOW.
 *
 * When no phase is given, generates random items with various date configurations
 * that may resolve to any phase.
 *
 * @param phase - Optional target lifecycle phase. If provided, dates are set to match.
 */
export function arbitraryDeprecationItem(
  phase?: LifecyclePhase
): fc.Arbitrary<DeprecationItem> {
  if (phase) {
    return arbitraryDeprecationItemForPhase(phase);
  }

  // No phase specified: pick a random phase and generate accordingly
  return fc
    .constantFrom(...LIFECYCLE_PHASES)
    .chain((randomPhase) => arbitraryDeprecationItemForPhase(randomPhase));
}

/**
 * Internal: generates a DeprecationItem whose dates resolve to the given phase.
 */
function arbitraryDeprecationItemForPhase(
  phase: LifecyclePhase
): fc.Arbitrary<DeprecationItem> {
  return fc.record({
    service_name: fc.constantFrom(...SERVICE_NAMES),
    item_id: fc.uuid(),
    status: fc.constantFrom(...DEPRECATION_STATUSES),
    source_url: fc.constant('https://docs.aws.amazon.com/example'),
    extraction_date: fc.constant(toISODateString(REFERENCE_NOW)),
    last_verified: fc.constant(toISODateString(REFERENCE_NOW)),
    service_specific: arbitraryServiceSpecificForPhase(phase),
  });
}

/**
 * Generates `service_specific` record with dates that cause the given phase
 * to be resolved by `determineLifecyclePhase`.
 *
 * Phase resolution priority (from engine.ts):
 *   1. block_create_date or block_update_date in the past → 'block_create_update'
 *   2. end_of_support_date in the past → 'end_of_life'
 *   3. end_of_standard_support_date in the past → 'end_of_standard_support'
 *   4. end_of_extended_support_date exists → 'extended_support'
 */
function arbitraryServiceSpecificForPhase(
  phase: LifecyclePhase
): fc.Arbitrary<Record<string, any>> {
  // Days in the past (positive = past offset from REFERENCE_NOW)
  const pastDays = fc.integer({ min: 1, max: 730 });
  // Days in the future
  const futureDays = fc.integer({ min: 1, max: 1000 });

  switch (phase) {
    case 'block_create_update':
      // block_create_date or block_update_date must be in the past
      // No higher-priority dates should override this (block is highest priority)
      return fc.record({
        block_create_date: pastDays.map((d) =>
          toISODateString(offsetDays(REFERENCE_NOW, -d))
        ),
        block_update_date: fc.oneof(
          pastDays.map((d) => toISODateString(offsetDays(REFERENCE_NOW, -d))),
          fc.constant(undefined)
        ),
        // These can be anything since block_create takes priority
        end_of_support_date: fc.constant(undefined),
        end_of_standard_support_date: fc.constant(undefined),
        end_of_extended_support_date: fc.constant(undefined),
        version: fc.string({ minLength: 1, maxLength: 10 }),
      });

    case 'end_of_life':
      // end_of_support_date in the past, but NO block dates in the past
      return fc.record({
        block_create_date: fc.constant(undefined),
        block_update_date: fc.constant(undefined),
        end_of_support_date: pastDays.map((d) =>
          toISODateString(offsetDays(REFERENCE_NOW, -d))
        ),
        end_of_standard_support_date: fc.oneof(
          pastDays.map((d) => toISODateString(offsetDays(REFERENCE_NOW, -d))),
          fc.constant(undefined)
        ),
        end_of_extended_support_date: fc.constant(undefined),
        version: fc.string({ minLength: 1, maxLength: 10 }),
      });

    case 'end_of_standard_support':
      // end_of_standard_support_date in the past, NO block or end_of_support in past
      return fc.record({
        block_create_date: fc.constant(undefined),
        block_update_date: fc.constant(undefined),
        end_of_support_date: fc.oneof(
          futureDays.map((d) => toISODateString(offsetDays(REFERENCE_NOW, d))),
          fc.constant(undefined)
        ),
        end_of_standard_support_date: pastDays.map((d) =>
          toISODateString(offsetDays(REFERENCE_NOW, -d))
        ),
        end_of_extended_support_date: fc.constant(undefined),
        version: fc.string({ minLength: 1, maxLength: 10 }),
      });

    case 'extended_support':
      // end_of_extended_support_date exists, NO higher-priority dates in the past
      return fc.record({
        block_create_date: fc.constant(undefined),
        block_update_date: fc.constant(undefined),
        end_of_support_date: fc.constant(undefined),
        end_of_standard_support_date: fc.constant(undefined),
        end_of_extended_support_date: futureDays.map((d) =>
          toISODateString(offsetDays(REFERENCE_NOW, d))
        ),
        version: fc.string({ minLength: 1, maxLength: 10 }),
      });
  }
}

// --- Generator: Impact ---

function arbitraryImpact(
  dimension?: Impact['dimension']
): fc.Arbitrary<Impact> {
  return fc.record({
    dimension: dimension
      ? fc.constant(dimension)
      : fc.constantFrom(...IMPACT_DIMENSIONS),
    severity: fc.constantFrom(...SEVERITIES),
    description: fc.string({ minLength: 5, maxLength: 100 }),
    details: fc.option(fc.string({ minLength: 5, maxLength: 200 }), {
      nil: undefined,
    }),
    sourceRef: fc.option(fc.string({ minLength: 3, maxLength: 30 }), {
      nil: undefined,
    }),
  });
}

// --- Generator: Recommendation ---

function arbitraryRecommendation(
  priority?: Recommendation['priority']
): fc.Arbitrary<Recommendation> {
  return fc.record({
    action: fc.string({ minLength: 5, maxLength: 100 }),
    effortLevel: fc.constantFrom(...EFFORT_LEVELS),
    priority: priority ? fc.constant(priority) : fc.constantFrom(...PRIORITIES),
    targetVersion: fc.option(fc.string({ minLength: 1, maxLength: 20 }), {
      nil: undefined,
    }),
    sourceRef: fc.option(fc.string({ minLength: 3, maxLength: 30 }), {
      nil: undefined,
    }),
  });
}

// --- Generator: ComplianceImpact ---

function arbitraryComplianceImpact(): fc.Arbitrary<ComplianceImpact> {
  return fc.record({
    framework: fc.constantFrom(...COMPLIANCE_FRAMEWORKS),
    controlReference: fc.string({ minLength: 3, maxLength: 20 }),
    description: fc.string({ minLength: 5, maxLength: 100 }),
    sourceRef: fc.string({ minLength: 5, maxLength: 50 }),
  });
}

// --- Generator: PricingTier ---

function arbitraryPricingTier(year: number): fc.Arbitrary<PricingTier> {
  return fc.record({
    year: fc.constant(year),
    surcharge: fc.double({ min: 0.01, max: 2.0, noNaN: true }).map(
      (v) => `$${v.toFixed(2)}`
    ),
    description: fc.string({ minLength: 5, maxLength: 50 }),
  });
}

// --- Generator: ExtendedSupportPricing ---

function arbitraryExtendedSupportPricing(): fc.Arbitrary<ExtendedSupportPricing> {
  return fc.record({
    service: fc.constantFrom('RDS', 'ElastiCache', 'EKS', 'OpenSearch', 'MemoryDB'),
    versions: fc.array(fc.string({ minLength: 1, maxLength: 10 }), {
      minLength: 1,
      maxLength: 3,
    }),
    tiers: fc.tuple(
      arbitraryPricingTier(1),
      arbitraryPricingTier(2),
      arbitraryPricingTier(3)
    ).map(([t1, t2, t3]) => [t1, t2, t3]),
    unit: fc.constantFrom('per vCPU-hour', 'per instance-hour', 'per node-hour'),
    effectiveDate: fc.constant(toISODateString(REFERENCE_NOW)),
    sourceRef: fc.string({ minLength: 5, maxLength: 50 }),
  });
}

// --- Generator: RiskMatrix ---

/**
 * Generates a valid RiskMatrix configuration with all required fields populated.
 *
 * The generated matrix:
 * - Has `phaseRiskMapping` for all 4 lifecycle phases
 * - Each phase has a consistent baseRiskLevel, impacts (including the primary dimension),
 *   recommendations (including correct priority), and compliance frameworks
 * - Has valid `temporalRules` with escalation (≤90 days) and de-minimis (>365 days) thresholds
 * - Has `extendedSupportPricing` entries
 * - Has a `sources` map
 */
export function arbitraryRiskMatrix(): fc.Arbitrary<RiskMatrix> {
  // Map phases to their expected base risk levels per the requirements
  const phaseRiskLevels: Record<LifecyclePhase, RiskLevel> = {
    extended_support: 'moyen',
    end_of_standard_support: 'élevé',
    end_of_life: 'critique',
    block_create_update: 'critique',
  };

  // Primary impact dimension per phase (from design Property 5)
  const phasePrimaryDimension: Record<LifecyclePhase, Impact['dimension']> = {
    extended_support: 'cost',
    end_of_standard_support: 'security',
    end_of_life: 'availability',
    block_create_update: 'availability',
  };

  // Primary recommendation priority per phase (from design Property 6)
  const phasePrimaryPriority: Record<LifecyclePhase, Recommendation['priority']> = {
    extended_support: 'monitor',
    end_of_standard_support: 'planned',
    end_of_life: 'immediate',
    block_create_update: 'immediate',
  };

  return fc
    .record({
      version: fc.constant('1.0'),
      lastUpdated: fc.constant(toISODateString(REFERENCE_NOW)),
      temporalRules: fc.record({
        escalationThresholdDays: fc.constant(90),
        deminimisThresholdDays: fc.constant(365),
      }),
      extendedSupportPricing: fc.array(arbitraryExtendedSupportPricing(), {
        minLength: 1,
        maxLength: 3,
      }),
      sources: fc.constant({
        'aws-extended-support': 'https://docs.aws.amazon.com/extended-support',
        'aws-shared-responsibility': 'https://aws.amazon.com/compliance/shared-responsibility-model/',
        'aws-support-policy': 'https://docs.aws.amazon.com/general/latest/gr/aws-service-information.html',
      }),
    })
    .chain((base) => {
      // Generate phase mappings ensuring each phase has its primary impact and recommendation
      const phaseArbEntries = LIFECYCLE_PHASES.map((phase) => {
        const primaryDimension = phasePrimaryDimension[phase];
        const primaryPriority = phasePrimaryPriority[phase];

        const impactsArb = fc
          .tuple(
            arbitraryImpact(primaryDimension), // Ensure at least one impact with primary dimension
            fc.array(arbitraryImpact(), { minLength: 0, maxLength: 3 })
          )
          .map(([primary, rest]) => [primary, ...rest]);

        const recommendationsArb = fc
          .tuple(
            arbitraryRecommendation(primaryPriority), // Ensure at least one with correct priority
            fc.array(arbitraryRecommendation(), { minLength: 0, maxLength: 2 })
          )
          .map(([primary, rest]) => [primary, ...rest]);

        // Compliance frameworks for advanced phases (end_of_standard_support+)
        const complianceArb =
          phase === 'extended_support'
            ? fc.constant(undefined as ComplianceImpact[] | undefined)
            : fc
                .array(arbitraryComplianceImpact(), { minLength: 1, maxLength: 3 })
                .map((items) => items as ComplianceImpact[] | undefined);

        return fc
          .tuple(impactsArb, recommendationsArb, complianceArb)
          .map(([impacts, recommendations, complianceFrameworks]) => ({
            phase,
            mapping: {
              baseRiskLevel: phaseRiskLevels[phase],
              impacts,
              recommendations,
              complianceFrameworks,
            },
          }));
      });

      return fc.tuple(...(phaseArbEntries as [
        fc.Arbitrary<any>,
        fc.Arbitrary<any>,
        fc.Arbitrary<any>,
        fc.Arbitrary<any>
      ])).map((entries) => {
        const phaseRiskMapping = {} as RiskMatrix['phaseRiskMapping'];
        for (const entry of entries) {
          (phaseRiskMapping as any)[entry.phase] = entry.mapping;
        }
        return {
          ...base,
          phaseRiskMapping,
        } as RiskMatrix;
      });
    });
}

// --- Generator: RiskAssessment (for sort testing) ---

/**
 * Generates a valid RiskAssessment object suitable for sort testing.
 *
 * The generated assessment has a valid `riskLevel` from the 4 possible values,
 * along with minimal populated fields needed for sorting and display.
 */
export function arbitraryRiskAssessment(): fc.Arbitrary<RiskAssessment> {
  return fc.record({
    itemId: fc.uuid(),
    serviceName: fc.constantFrom(...SERVICE_NAMES),
    riskLevel: fc.constantFrom(...RISK_LEVELS),
    baseRiskLevel: fc.constantFrom(...RISK_LEVELS),
    wasEscalated: fc.boolean(),
    lifecyclePhase: fc.constantFrom(...LIFECYCLE_PHASES),
    phaseSource: fc.constantFrom('date' as const, 'status' as const),
    daysToNextMilestone: fc.option(fc.integer({ min: 0, max: 1000 }), { nil: null }),
    nextMilestoneLabel: fc.option(fc.string({ minLength: 3, maxLength: 30 }), {
      nil: null,
    }),
    nextMilestoneDate: fc.option(
      fc.date({ min: REFERENCE_NOW, max: offsetDays(REFERENCE_NOW, 1000) }),
      { nil: null }
    ),
    impacts: fc.array(arbitraryImpact(), { minLength: 0, maxLength: 4 }),
    recommendations: fc.array(arbitraryRecommendation(), {
      minLength: 0,
      maxLength: 3,
    }),
    complianceImpacts: fc.array(arbitraryComplianceImpact(), {
      minLength: 0,
      maxLength: 2,
    }),
    pricingInfo: fc.option(arbitraryExtendedSupportPricing(), { nil: null }),
    tooltipSummary: fc.string({ minLength: 5, maxLength: 80 }),
  });
}

// --- Exported reference date for test consumers ---

/**
 * The reference date used internally by phase-targeted generators.
 * Tests should pass this as `referenceDate` to engine functions
 * to ensure date comparisons align with generated items.
 */
export const GENERATOR_REFERENCE_DATE = REFERENCE_NOW;
