// The ONE place the fleet is tallied along its two axes: the Deprecation axis
// (red / yellow / active backlog / migrated, from the Trusted Advisor
// alert_status and the inferred-MIGRATED state) and the migration-run axis
// (the mig# enum merged onto rows as migration_run_status). My functions (the
// summary strip) and Statistics (the pipeline chart, the status pie, the KPIs)
// both read these counts, so the two pages can never disagree on a number.
// Scope is the caller's business: it passes the rows it wants counted (the
// toggled set on My functions, the active backlog on Statistics).
import type { MigrationFunction } from './api';

// Active backlog: flagged Red (past EOL) or Yellow (approaching) by Trusted
// Advisor and not yet resolved. Resolved / Green rows are no longer backlog.
export const isActiveBacklog = (f: MigrationFunction): boolean => {
  const alert = (f.alert_status || '').toLowerCase();
  return !f.resolved_at && (alert === 'red' || alert === 'yellow');
};

export interface MigrationTallies {
  // Deprecation axis (active rows only for red / yellow / active).
  red: number;
  yellow: number;
  active: number;
  migrated: number;       // inferred-green migrated functions (migration_status === 'MIGRATED')
  // Migration-run axis (mig# enum), over every row passed.
  notStarted: number;     // active backlog with NO migration run at all
  runInFlight: number;    // QUEUED | IN_PROGRESS
  runRejected: number;    // triaged out before any build
  runFailed: number;      // transform / push / timeout
  runNoChanges: number;   // AWS Transform ran and found nothing to change
  runBranchReady: number; // the upgrade branch is pushed
  runLegacy: number;      // REHEARSED / PR_OPENED / unknown (D8)
}

export function tallyMigrationRuns(rows: MigrationFunction[]): MigrationTallies {
  // Split the ACTIVE backlog's Trusted Advisor alert_status: Red = already past
  // EOL, Yellow = approaching deprecation. Resolved/green rows are excluded from
  // red/yellow (they are no longer backlog), but stay countable as migrated.
  let red = 0;
  let yellow = 0;
  let active = 0;     // active backlog headline: Red/Yellow and not resolved
  let migrated = 0;   // Migration axis: inferred-green migrated functions
  // Phase-2 migration-run axis (mig# enum): in-flight runs, rejected (triaged
  // out before any build), failed (transform / push / timeout), no changes
  // and branches ready (the upgrade branch is pushed).
  let runInFlight = 0;
  let runRejected = 0;
  let runFailed = 0;
  let runNoChanges = 0;
  let runBranchReady = 0;
  let runLegacy = 0;
  let notStarted = 0;   // active backlog with NO migration run at all
  for (const r of rows) {
    if (isActiveBacklog(r)) {
      active += 1;
      const alert = (r.alert_status || '').toLowerCase();
      if (alert === 'red') red += 1; else if (alert === 'yellow') yellow += 1;
    }
    if (r.migration_status === 'MIGRATED') migrated += 1;
    const run: string | undefined = r.migration_run_status;
    if (run === 'QUEUED' || run === 'IN_PROGRESS') runInFlight += 1;
    else if (run === 'REJECTED') runRejected += 1;
    // FAILED only: rejections are triage, and NO_CHANGES (AWS Transform ran
    // and found nothing to change) is not a failure, so neither is counted.
    else if (run === 'FAILED') runFailed += 1;
    else if (run === 'NO_CHANGES') runNoChanges += 1;
    else if (run === 'BRANCH_READY') runBranchReady += 1;
    // Not started = an active-backlog function nobody has tried to migrate
    // yet. It is NOT the whole backlog: a rejected or failed run is a run.
    // Not started + In progress + Rejected + Failed + No changes + Branch
    // ready (+ Legacy) = active backlog.
    else if (!run || run === 'NOT_STARTED') { if (isActiveBacklog(r)) notStarted += 1; }
    // Legacy tolerance (D8): REHEARSED / PR_OPENED rows written by the
    // previous flow are counted apart so the pages can say so, never as a
    // run status this build knows; they leave once the function is migrated again.
    else runLegacy += 1;
  }
  return { red, yellow, active, migrated, notStarted,
           runInFlight, runRejected, runFailed, runNoChanges, runBranchReady, runLegacy };
}
