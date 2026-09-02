/**
 * Core TypeScript interfaces and types for the Operational Risk Assessment engine.
 *
 * These types define the risk matrix configuration, computed assessment outputs,
 * and UI integration contracts. All risk levels use French labels as per
 * operational conventions.
 */

// --- Enumerations / Union Types ---

/**
 * Risk severity levels in French.
 * Ordered from highest to lowest: critique > élevé > moyen > faible
 */
export type RiskLevel = 'critique' | 'élevé' | 'moyen' | 'faible';

/**
 * Lifecycle phases for AWS service deprecation items.
 */
export type LifecyclePhase =
  | 'extended_support'
  | 'end_of_standard_support'
  | 'end_of_life'
  | 'block_create_update';

// --- Risk Matrix Configuration Types ---

/**
 * Top-level risk matrix configuration loaded from risk-matrix.json.
 * Contains phase-to-risk mappings, temporal rules, pricing data, and sources.
 */
export interface RiskMatrix {
  /** Schema version for forward compatibility */
  version: string;
  /** ISO date of last update */
  lastUpdated: string;

  /** Mapping from lifecycle phase to risk configuration */
  phaseRiskMapping: {
    [phase in LifecyclePhase]: {
      baseRiskLevel: RiskLevel;
      impacts: Impact[];
      recommendations: Recommendation[];
      complianceFrameworks?: ComplianceImpact[];
    };
  };

  /** Temporal escalation and de-minimis rules */
  temporalRules: {
    /** Days threshold for escalation (e.g., 90 days → escalate) */
    escalationThresholdDays: number;
    /** Days threshold for de-minimis (e.g., 365 days → faible) */
    deminimisThresholdDays: number;
  };

  /** Extended support pricing data per service */
  extendedSupportPricing: ExtendedSupportPricing[];

  /** Named references → AWS documentation URLs */
  sources: {
    [key: string]: string;
  };
}

// --- Impact and Recommendation Types ---

/**
 * A single operational impact within one of the four dimensions.
 */
export interface Impact {
  /** Impact dimension category */
  dimension: 'cost' | 'security' | 'compliance' | 'availability';
  /** Severity relative to this dimension */
  severity: 'critical' | 'warning' | 'info';
  /** Human-readable impact description */
  description: string;
  /** Optional detailed explanation */
  details?: string;
  /** Key into the RiskMatrix sources map */
  sourceRef?: string;
}

/**
 * An actionable recommendation for addressing risk.
 */
export interface Recommendation {
  /** Description of the recommended action */
  action: string;
  /** Estimated effort to implement */
  effortLevel: 'low' | 'medium' | 'high';
  /** Urgency/priority of the recommendation */
  priority: 'immediate' | 'planned' | 'monitor';
  /** Target version to upgrade to (e.g., "latest LTS") */
  targetVersion?: string;
  /** Key into the RiskMatrix sources map */
  sourceRef?: string;
}

/**
 * Compliance framework impact with control reference.
 */
export interface ComplianceImpact {
  /** Affected compliance framework */
  framework: 'SOC2' | 'PCI-DSS' | 'HIPAA';
  /** Specific control reference within the framework */
  controlReference: string;
  /** Description of the compliance impact */
  description: string;
  /** Key into the RiskMatrix sources map */
  sourceRef?: string;
}

// --- Extended Support Pricing Types ---

/**
 * Extended Support pricing data for a specific AWS service.
 */
export interface ExtendedSupportPricing {
  /** AWS service name (e.g., "RDS", "ElastiCache") */
  service: string;
  /** Affected version identifiers */
  versions: string[];
  /** Year-over-year pricing tiers */
  tiers: PricingTier[];
  /** Pricing unit (e.g., "per vCPU-hour", "per instance-hour") */
  unit: string;
  /** ISO date when pricing takes effect */
  effectiveDate: string;
  /** Key into the RiskMatrix sources map */
  sourceRef: string;
}

/**
 * A single year tier within Extended Support pricing.
 */
export interface PricingTier {
  /** Year number (1, 2, or 3) */
  year: number;
  /** Surcharge amount (e.g., "$0.10") */
  surcharge: string;
  /** Human-readable description of this tier */
  description: string;
}

// --- Computed Assessment Output Types ---

/**
 * Complete risk assessment output for a single deprecation item.
 * Produced by the RiskAssessmentEngine.
 */
export interface RiskAssessment {
  /** Unique identifier of the assessed item */
  itemId: string;
  /** AWS service name */
  serviceName: string;

  // Computed risk
  /** Final computed risk level (after temporal escalation) */
  riskLevel: RiskLevel;
  /** Base risk level from phase mapping (before temporal escalation) */
  baseRiskLevel: RiskLevel;
  /** Whether temporal rules elevated the risk level */
  wasEscalated: boolean;

  // Phase determination
  /** Determined lifecycle phase */
  lifecyclePhase: LifecyclePhase;
  /** How the phase was determined */
  phaseSource: 'date' | 'status';

  // Temporal context
  /** Days until the next milestone (null if no future milestones) */
  daysToNextMilestone: number | null;
  /** Human-readable label for the next milestone */
  nextMilestoneLabel: string | null;
  /** Date of the next milestone */
  nextMilestoneDate: Date | null;

  // Impacts from matrix
  /** Operational impacts for this item's phase */
  impacts: Impact[];

  // Recommendations from matrix
  /** Action recommendations for this item's phase */
  recommendations: Recommendation[];

  // Compliance impacts (if applicable)
  /** Compliance framework impacts */
  complianceImpacts: ComplianceImpact[];

  // Pricing (if extended support)
  /** Extended support pricing info (null if not applicable) */
  pricingInfo: ExtendedSupportPricing | null;

  // Tooltip summary
  /** Single-line summary for tooltip display */
  tooltipSummary: string;
}

/**
 * Pre-filled data for creating an Action Plan from a risk assessment.
 * Used when navigating to the PlanOfAction page.
 */
export interface ActionPlanPrefill {
  /** AWS service name */
  service_name: string;
  /** Deprecation item identifier */
  item_id: string;
  /** Human-readable item name */
  item_name: string;
  /** Recommended priority based on risk level */
  priority: 'low' | 'medium' | 'high' | 'critical';
  /** Pre-filled notes with risk summary and recommendation */
  notes: string;
}
