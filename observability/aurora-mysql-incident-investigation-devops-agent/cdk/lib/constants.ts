/**
 * Values shared by the database stack (what CDK creates) and the Lab stack (what the Lab
 * restores on revert). One definition, passed to the Lab as environment variables, so a
 * revert can never put back something different from what the deploy created.
 */

/**
 * The cluster's business tags (lab/scenarios.yaml, header comment). The skill reads them;
 * the Lab removes them (business-tags-removed) or changes DataClassification
 * (compliance-tag-gdpr) and restores exactly these values on revert.
 */
export const BUSINESS_TAGS: Record<string, string> = {
  DependentServices: 'checkout-service:CRITICAL - order-history-api:HIGH - analytics-etl:MEDIUM',
  OnCallTeam: 'Data Platform / DBRE',
  RevenuePerMinute: '5100',
  SLAThresholdMinutes: '20',
  DataClassification: 'Confidential',
};

/** max_connections of Aurora MySQL on db.t4g.medium (default parameter group). */
export const MAX_CONNECTIONS = 90;

/** Connections the load function tries to hold: it stops earlier when the writer refuses (error 1040). */
export const LOAD_TARGET_CONNECTIONS = 85;

/** DatabaseConnections alarm: Maximum above this for 2 consecutive one-minute datapoints. */
export const CONNECTIONS_THRESHOLD = 60;

/** Reserved concurrency of the load function while the storm is on (one runs, one hands over). */
export const LOAD_CONCURRENCY_ON = 2;

/**
 * Safety net: the load function stops on its own after this long, even if nothing reverted it.
 * Longer than the longest auto-revert in lab/scenarios.yaml (1800 s).
 */
export const LOAD_MAX_STORM_SECONDS = 3600;
