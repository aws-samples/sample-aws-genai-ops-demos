/**
 * Shared constants for migration status definitions.
 * Single source of truth — imported by Dashboard, Functions, MigrationPlan.
 */

export type StatusIndicatorType = 'success' | 'error' | 'warning' | 'info' | 'stopped' | 'pending' | 'in-progress' | 'loading';

export interface StatusConfig {
  label: string;
  type: StatusIndicatorType;
}

export const MIGRATION_STATUS: Record<string, StatusConfig> = {
  DISCOVERED:        { label: 'Discovered',        type: 'pending' },
  ANALYZING:         { label: 'Analyzing',         type: 'in-progress' },
  ASSESSED:          { label: 'Assessed',          type: 'info' },
  TRANSFORMING:      { label: 'Transforming',      type: 'in-progress' },
  READY_TO_MIGRATE:  { label: 'Ready to Migrate',  type: 'success' },
  TRANSFORM_FAILED:  { label: 'Transform Failed',  type: 'error' },
  SKIPPED:           { label: 'Skipped',           type: 'stopped' },
  RESOLVED:          { label: 'Resolved',          type: 'success' },
};

export function getStatusConfig(status: string): StatusConfig {
  return MIGRATION_STATUS[status] || { label: status, type: 'info' };
}

/**
 * Trusted Advisor alert status badge colors.
 */
export const ALERT_STATUS: Record<string, { label: string; color: string }> = {
  red:    { label: 'Red',    color: 'severity-high' },
  yellow: { label: 'Yellow', color: 'severity-low' },
  green:  { label: 'Green',  color: 'severity-neutral' },
};

export function getAlertConfig(alertStatus: string): { label: string; color: string } {
  return ALERT_STATUS[alertStatus.toLowerCase()] || { label: alertStatus || '—', color: 'grey' };
}

/**
 * Priority label badge colors.
 */
export const PRIORITY_COLORS: Record<string, string> = {
  CRITICAL: 'severity-critical',
  HIGH:     'severity-high',
  MEDIUM:   'severity-medium',
  LOW:      'severity-low',
  INACTIVE: 'severity-neutral',
};
