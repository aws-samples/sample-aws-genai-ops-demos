/**
 * Risk Assessment Engine — core computation logic.
 *
 * Pure function module with no side effects or state.
 * Handles risk computation for AWS service lifecycle items.
 */

import type { DeprecationItem } from '../api';
import type {
  LifecyclePhase,
  RiskLevel,
  RiskMatrix,
  RiskAssessment,
  ExtendedSupportPricing,
} from './types';

// --- Helpers ---

/**
 * Parse a date string into a Date object.
 * Handles various formats from the backend: ISO 8601 strings,
 * "YYYY-MM-DD", "YYYY/MM/DD", and epoch-like numeric strings.
 * Returns null if the string is empty, undefined, or unparseable.
 */
export function parseDate(value: string | null | undefined): Date | null {
  if (value == null || value.trim() === '') {
    return null;
  }

  const trimmed = value.trim();

  // Try native Date parse (handles ISO 8601 and most standard formats)
  const parsed = new Date(trimmed);
  if (!isNaN(parsed.getTime())) {
    return parsed;
  }

  return null;
}

/**
 * Returns true if the given date is in the past relative to referenceDate.
 */
function isInPast(date: Date, referenceDate: Date): boolean {
  return date.getTime() <= referenceDate.getTime();
}

// --- Lifecycle Phase Determination ---

/**
 * Result of determining the lifecycle phase for an item.
 */
export interface PhaseResult {
  phase: LifecyclePhase;
  source: 'date' | 'status';
}

/**
 * Determine the lifecycle phase of a deprecation item.
 *
 * Priority order (from dates):
 *   1. block_create_date or block_update_date in the past → 'block_create_update'
 *   2. end_of_support_date in the past → 'end_of_life'
 *   3. end_of_standard_support_date in the past → 'end_of_standard_support'
 *   4. end_of_extended_support_date exists (future or past) → 'extended_support'
 *
 * Fallback (when no usable dates):
 *   Maps item.status to a phase:
 *     - 'end_of_life' → 'end_of_life'
 *     - 'deprecated' → 'end_of_standard_support'
 *     - 'extended_support' → 'extended_support'
 *     - anything else → 'end_of_standard_support' (conservative default)
 *
 * @param item - The deprecation item to assess
 * @param referenceDate - Date to compare against (defaults to now)
 */
export function determineLifecyclePhase(
  item: DeprecationItem,
  referenceDate: Date = new Date()
): PhaseResult {
  const sp = item.service_specific || {};

  // Attempt date-based determination
  const blockCreateDate = parseDate(sp.block_create_date);
  const blockUpdateDate = parseDate(sp.block_update_date);
  const endOfSupportDate = parseDate(sp.end_of_support_date);
  const endOfStandardSupportDate = parseDate(sp.end_of_standard_support_date);
  const endOfExtendedSupportDate = parseDate(sp.end_of_extended_support_date);

  // 1. block_create or block_update in the past → block_create_update
  if (
    (blockCreateDate && isInPast(blockCreateDate, referenceDate)) ||
    (blockUpdateDate && isInPast(blockUpdateDate, referenceDate))
  ) {
    return { phase: 'block_create_update', source: 'date' };
  }

  // 2. end_of_support_date in the past → end_of_life
  if (endOfSupportDate && isInPast(endOfSupportDate, referenceDate)) {
    return { phase: 'end_of_life', source: 'date' };
  }

  // 3. end_of_standard_support_date in the past → end_of_standard_support
  if (endOfStandardSupportDate && isInPast(endOfStandardSupportDate, referenceDate)) {
    return { phase: 'end_of_standard_support', source: 'date' };
  }

  // 4. end_of_extended_support_date exists → extended_support
  if (endOfExtendedSupportDate) {
    return { phase: 'extended_support', source: 'date' };
  }

  // Fallback: use item.status
  return { phase: mapStatusToPhase(item.status), source: 'status' };
}

/**
 * Maps a DeprecationItem status string to a LifecyclePhase.
 * Defaults to 'end_of_standard_support' for unexpected values (conservative).
 */
function mapStatusToPhase(status: string): LifecyclePhase {
  switch (status) {
    case 'end_of_life':
      return 'end_of_life';
    case 'extended_support':
      return 'extended_support';
    case 'deprecated':
      return 'end_of_standard_support';
    default:
      // Conservative default for unexpected status values
      return 'end_of_standard_support';
  }
}

// --- Temporal Escalation ---

/** Ordered risk levels from lowest to highest severity. */
const RISK_LEVEL_ORDER: RiskLevel[] = ['faible', 'moyen', 'élevé', 'critique'];

/** Days threshold: within this many days, escalate risk one tier. */
const ESCALATION_THRESHOLD_DAYS = 90;

/** Days threshold: beyond this many days for ALL milestones, risk is always "faible". */
const DEMINIMIS_THRESHOLD_DAYS = 365;

/**
 * Apply temporal escalation to a base risk level based on proximity to the next milestone.
 *
 * Rules:
 * - If `daysToMilestone` is null → no escalation; return base level unchanged.
 * - If `daysToMilestone` > 365 → de-minimis rule: return "faible" regardless of base level.
 * - If `daysToMilestone` ≤ 90 → escalate one tier (faible→moyen, moyen→élevé, élevé→critique).
 * - Never exceeds "critique".
 * - Between 90 and 365 days (exclusive) → no escalation; return base level unchanged.
 *
 * @param baseLevel - The risk level determined by lifecycle phase (from the matrix)
 * @param daysToMilestone - Days until the next milestone, or null if no future milestone exists
 * @returns The final risk level after temporal adjustment
 */
export function applyTemporalEscalation(
  baseLevel: RiskLevel,
  daysToMilestone: number | null
): RiskLevel {
  // No milestone information → no escalation
  if (daysToMilestone === null) {
    return baseLevel;
  }

  // De-minimis rule: all milestones beyond 365 days → always "faible"
  if (daysToMilestone > DEMINIMIS_THRESHOLD_DAYS) {
    return 'faible';
  }

  // Escalation rule: within 90 days → bump one tier
  if (daysToMilestone <= ESCALATION_THRESHOLD_DAYS) {
    const currentIndex = RISK_LEVEL_ORDER.indexOf(baseLevel);
    // Move up one tier, capped at the highest index (critique)
    const escalatedIndex = Math.min(currentIndex + 1, RISK_LEVEL_ORDER.length - 1);
    return RISK_LEVEL_ORDER[escalatedIndex];
  }

  // Between 90 and 365 days → no change
  return baseLevel;
}


// --- Next Milestone Calculation ---

/**
 * Milestone candidate with its label and parsed date.
 */
interface MilestoneCandidate {
  label: string;
  date: Date;
}

/**
 * Find the closest FUTURE milestone date from the item's service_specific fields.
 *
 * Considered fields:
 * - deprecation_date
 * - end_of_standard_support_date
 * - end_of_support_date
 * - end_of_extended_support_date
 * - block_create_date
 * - block_update_date
 *
 * @returns The closest future milestone, or null if none exist.
 */
function findNextMilestone(
  item: DeprecationItem,
  referenceDate: Date
): { daysTo: number; label: string; date: Date } | null {
  const sp = item.service_specific || {};

  const candidates: MilestoneCandidate[] = [];

  const fieldMap: Array<[string, string]> = [
    ['deprecation_date', 'Deprecation'],
    ['end_of_standard_support_date', 'Fin du support standard'],
    ['end_of_support_date', 'Fin de support'],
    ['end_of_extended_support_date', 'Fin du support étendu'],
    ['block_create_date', 'Blocage création'],
    ['block_update_date', 'Blocage modification'],
  ];

  for (const [field, label] of fieldMap) {
    const parsed = parseDate(sp[field]);
    if (parsed && parsed.getTime() > referenceDate.getTime()) {
      candidates.push({ label, date: parsed });
    }
  }

  if (candidates.length === 0) {
    return null;
  }

  // Sort by date ascending, pick the closest
  candidates.sort((a, b) => a.date.getTime() - b.date.getTime());
  const closest = candidates[0];

  const msPerDay = 1000 * 60 * 60 * 24;
  const daysTo = Math.ceil(
    (closest.date.getTime() - referenceDate.getTime()) / msPerDay
  );

  return { daysTo, label: closest.label, date: closest.date };
}

// --- Tooltip Summary Generation ---

/**
 * Generate a single-line French tooltip summary based on lifecycle phase.
 */
function generateTooltipSummary(phase: LifecyclePhase): string {
  switch (phase) {
    case 'extended_support':
      return 'Impact financier: surcoût Extended Support';
    case 'end_of_standard_support':
      return 'Risque sécurité: aucun patch automatique';
    case 'end_of_life':
      return 'Risque critique: aucun support AWS';
    case 'block_create_update':
      return 'Blocage opérationnel: création/modification impossible';
  }
}

// --- Pricing Lookup ---

/**
 * Find extended support pricing info for a given service name.
 * Performs a case-insensitive partial match on the service name.
 */
function findPricingInfo(
  serviceName: string,
  pricing: ExtendedSupportPricing[]
): ExtendedSupportPricing | null {
  const normalised = serviceName.toLowerCase();
  return (
    pricing.find((p) => normalised.includes(p.service.toLowerCase())) ?? null
  );
}

// --- Batch Assessment and Sorting ---

/**
 * Compute risk assessments for all items in a batch.
 *
 * Simply maps `assessRisk` over the items array.
 *
 * @param items - Array of deprecation items to assess
 * @param matrix - The risk matrix configuration
 * @param referenceDate - Reference date for calculations (defaults to now)
 * @returns Array of RiskAssessment objects, one per item
 */
export function assessAll(
  items: DeprecationItem[],
  matrix: RiskMatrix,
  referenceDate: Date = new Date()
): RiskAssessment[] {
  return items.map((item) => assessRisk(item, matrix, referenceDate));
}

/**
 * Sort risk assessments in descending risk order (critique first, faible last).
 *
 * Total order: critique > élevé > moyen > faible.
 * Items with missing or invalid risk levels are placed at the end of the sorted list.
 *
 * @param assessments - Array of RiskAssessment objects to sort
 * @returns New sorted array (does not mutate the input)
 */
export function sortByRisk(assessments: RiskAssessment[]): RiskAssessment[] {
  return [...assessments].sort((a, b) => {
    const indexA = RISK_LEVEL_ORDER.indexOf(a.riskLevel);
    const indexB = RISK_LEVEL_ORDER.indexOf(b.riskLevel);

    // Items with missing/invalid risk levels get -1 from indexOf;
    // push them to the end by treating -1 as a very low priority.
    const effectiveA = indexA === -1 ? -1 : indexA;
    const effectiveB = indexB === -1 ? -1 : indexB;

    // Sort descending: higher index (more critical) comes first
    return effectiveB - effectiveA;
  });
}

// --- Main Assessment Function ---

/**
 * Compute a full risk assessment for a single deprecation item.
 *
 * Steps:
 * 1. Determine lifecycle phase from item dates/status
 * 2. Look up base risk level from the matrix's phaseRiskMapping
 * 3. Calculate days to the closest future milestone
 * 4. Apply temporal escalation (90-day escalation, 365-day de-minimis)
 * 5. Attach impacts, recommendations, compliance impacts from matrix
 * 6. Attach pricing info if phase is extended_support and service matches
 * 7. Generate tooltip summary
 *
 * @param item - The deprecation item to assess
 * @param matrix - The risk matrix configuration
 * @param referenceDate - Reference date for calculations (defaults to now)
 * @returns A complete RiskAssessment object
 */
export function assessRisk(
  item: DeprecationItem,
  matrix: RiskMatrix,
  referenceDate: Date = new Date()
): RiskAssessment {
  // 1. Determine lifecycle phase
  const phaseResult = determineLifecyclePhase(item, referenceDate);
  const { phase, source: phaseSource } = phaseResult;

  // 2. Look up base risk level from matrix
  const phaseConfig = matrix.phaseRiskMapping[phase];
  const baseRiskLevel = phaseConfig.baseRiskLevel;

  // 3. Calculate days to next milestone
  const milestone = findNextMilestone(item, referenceDate);
  const daysToNextMilestone = milestone?.daysTo ?? null;
  const nextMilestoneLabel = milestone?.label ?? null;
  const nextMilestoneDate = milestone?.date ?? null;

  // 4. Apply temporal escalation
  const riskLevel = applyTemporalEscalation(baseRiskLevel, daysToNextMilestone);
  const wasEscalated = riskLevel !== baseRiskLevel;

  // 5. Attach impacts, recommendations, compliance impacts from matrix
  const impacts = phaseConfig.impacts ?? [];
  const recommendations = phaseConfig.recommendations ?? [];
  const complianceImpacts = phaseConfig.complianceFrameworks ?? [];

  // 6. Attach pricing info for extended_support phase
  let pricingInfo: ExtendedSupportPricing | null = null;
  if (phase === 'extended_support' && matrix.extendedSupportPricing?.length > 0) {
    pricingInfo = findPricingInfo(item.service_name, matrix.extendedSupportPricing);
  }

  // 7. Generate tooltip summary
  const tooltipSummary = generateTooltipSummary(phase);

  return {
    itemId: item.item_id,
    serviceName: item.service_name,
    riskLevel,
    baseRiskLevel,
    wasEscalated,
    lifecyclePhase: phase,
    phaseSource,
    daysToNextMilestone,
    nextMilestoneLabel,
    nextMilestoneDate,
    impacts,
    recommendations,
    complianceImpacts,
    pricingInfo,
    tooltipSummary,
  };
}
