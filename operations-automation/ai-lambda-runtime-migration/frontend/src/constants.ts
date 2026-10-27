/**
 * Shared constants for migration status definitions.
 * Single source of truth — imported by My functions, the Activity log and the
 * How it works page, so a label or an indicator can never drift between them.
 */
import type { StatusIndicatorProps } from '@cloudscape-design/components/status-indicator';
import type { MigrationBatchSummary, MigrationRunStatus } from './api';

type StatusIndicatorType = 'success' | 'error' | 'warning' | 'info' | 'stopped' | 'pending' | 'in-progress' | 'loading' | 'not-started';

export interface StatusConfig {
  label: string;
  type: StatusIndicatorType;
}

// Tag key a migration reads the source repository name from when Settings
// has never been saved (the backend default; shown in Settings and in the
// Migrate help text).
export const DEFAULT_REPO_TAG = 'source-repo';

// Backend _priority_label() returns UPPERCASE labels (CRITICAL/HIGH/MEDIUM/LOW/
// INACTIVE). All status-like columns use StatusIndicator (consistent with the
// Deprecation column), so map each priority to a StatusIndicator type.
export const PRIORITY_INDICATOR: Record<string, StatusIndicatorProps.Type> = {
  CRITICAL: 'error', HIGH: 'warning', MEDIUM: 'warning',
  LOW: 'info', INACTIVE: 'pending',
};

// Trusted Advisor alert colours, shown with their meaning in the PropertyFilter
// token instead of the raw colour: Red = already past EOL, Yellow = approaching,
// Green = migrated (Trusted Advisor stopped reporting it; stored as MIGRATED).
export const ALERT_STATUS_LABEL: Record<string, string> = {
  Red: 'Already deprecated (past EOL)',
  Yellow: 'Approaching deprecation',
  Green: 'Migrated',
};

// Deprecation axis (the fn# row's own migration_status, written by the scan).
// MIGRATED is inferred when Trusted Advisor stops reporting a function; the
// migration RUN axis below is a distinct enum on the mig# item.
export const MIGRATION_STATUS: Record<string, StatusConfig> = {
  DISCOVERED: { label: 'Discovered', type: 'pending' },
  ASSESSED:   { label: 'Assessed',   type: 'info' },
  SKIPPED:    { label: 'Skipped',    type: 'stopped' },
  MIGRATED:   { label: 'Migrated',   type: 'success' },
};

// Migration-run axis (the mig# item enum, merged onto rows as
// migration_run_status). Single source for the table, drawer, hero, Activity
// log and How it works.
export const MIGRATION_RUN_STATUS: Record<MigrationRunStatus, StatusConfig> = {
  NOT_STARTED:  { label: 'Not started',  type: 'not-started' },
  QUEUED:       { label: 'Queued',       type: 'in-progress' },
  IN_PROGRESS:  { label: 'In progress',  type: 'in-progress' },
  REJECTED:     { label: 'Rejected',     type: 'stopped' },
  FAILED:       { label: 'Failed',       type: 'error' },
  NO_CHANGES:   { label: 'No changes',   type: 'info' },
  BRANCH_READY: { label: 'Branch ready', type: 'success' },
};

// Unknown-value guard: a status this build does not know renders with its raw
// value and never throws.
export const migrationRunConfig = (status: string | undefined): StatusConfig =>
  MIGRATION_RUN_STATUS[(status || 'NOT_STARTED') as MigrationRunStatus]
  ?? { label: String(status), type: 'pending' };

// Rejection (pre-build triage) and failure (build / transform / push) codes
// carried by the mig# item's migration_failure_code. Defined ONCE here: the
// table popover, the drawer and the How it works page all read this map.
export interface FailureCodeHelp {
  label: string;
  meaning: string;
  next: string;
}

export const MIGRATION_FAILURE_CODES: Record<string, FailureCodeHelp> = {
  TAG_NOT_FOUND: {
    label: 'No repository tag',
    meaning: 'The function has no tag with the key configured in Settings (default source-repo).',
    next: 'Tag the function with its repository name and migrate again.',
  },
  SOURCE_NOT_FOUND: {
    label: 'Repository not found',
    meaning: "The tagged repository does not exist in the function's region.",
    next: 'Fix the tag value or create the repository, then migrate again.',
  },
  BRANCH_NOT_FOUND: {
    label: 'Branch not found',
    meaning: 'The branch tag names a branch the repository does not have.',
    next: 'Fix the branch tag or push the branch, then migrate again.',
  },
  RUNTIME_UNSUPPORTED: {
    label: 'Runtime not supported',
    meaning: 'No AWS Transform managed transformation covers this runtime (for example Go, Ruby or Java; Python, Node.js and .NET are covered).',
    next: 'Migrate it by hand or wait for a transformation.',
  },
  ACCESS_DENIED: {
    label: 'Access denied',
    meaning: 'The orchestrator could not read the repository (the demo grants access to the fixture repository in the deploy region only), or the CodeBuild project is not deployed.',
    next: "Deploy the Transform build plane and check the orchestrator role's CodeCommit grant.",
  },
  ALREADY_IN_PROGRESS: {
    label: 'Already in progress',
    meaning: 'Another batch is still migrating this function.',
    next: 'Wait for that batch to finish, then migrate again.',
  },
  UNKNOWN: {
    label: 'Unknown',
    meaning: 'The per-function unit crashed past its retries before reporting.',
    next: "Check the orchestrator's CloudWatch logs for the batch.",
  },
  TRANSFORM_FAILED: {
    label: 'Transformation failed',
    meaning: 'AWS Transform reported an error, or the build ended without a result.',
    next: "Open the build in CodeBuild; the log tail is in the drawer's diff summary.",
  },
  PUSH_FAILED: {
    label: 'Push failed',
    meaning: 'The transformation succeeded but git push of the upgrade branch failed; nothing was pushed.',
    next: "Check the CodeBuild role's push permission and the repository, then migrate again.",
  },
  TIMED_OUT: {
    label: 'Timed out',
    meaning: 'The build or its report exceeded the 60 min build / 75 min callback bound.',
    next: 'Check the build in CodeBuild; migrate again once the cause is fixed.',
  },
};

export const failureCodeHelp = (code: string | undefined): FailureCodeHelp | undefined =>
  code ? MIGRATION_FAILURE_CODES[code] : undefined;

// One-liner shared by the Steps card header (finished), the finish flash and
// the Activity log: "2 selected, 1 rejected, 1 branch ready". Zero segments are
// omitted; "selected" is always shown.
export const migrationSummaryLine = (s: MigrationBatchSummary): string => {
  const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);
  const segments = [`${s.total_selected ?? 0} selected`];
  if (s.over_cap_skipped) segments.push(`${s.over_cap_skipped} over cap`);
  if (s.rejected) segments.push(`${s.rejected} rejected`);
  if (s.branches_ready) segments.push(`${s.branches_ready} ${plural(s.branches_ready, 'branch', 'branches')} ready`);
  if (s.no_changes) segments.push(`${s.no_changes} no changes`);
  if (s.failed) segments.push(`${s.failed} failed`);
  return segments.join(', ');
};
