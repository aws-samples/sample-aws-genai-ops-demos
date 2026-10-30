// "Activity log" timeline: the last few scan (durable pipeline) executions AND
// migration batches, interleaved newest-first, as a Cloudscape gen-AI Event log —
// a chronological, grouped list of what each scan did or skipped and how each
// migration batch ended (each batch is ONE entry; the per-function outcome
// lives in the My functions table). Follows the Cloudscape timeline pattern:
// Steps with status 'log', a fixed-width timestamp column, a status icon, and a
// concise past-tense label; grouped by day with Divider; an optional Select
// filter (All / Today / Yesterday). Each entry is ONE compact line (timestamp +
// message, as in a Cloudscape event log); a short 'Discover ·' / 'Migration ·' text prefix
// tells the two kinds apart, and any detail is folded into the same line.
// Rendered as the "Activity log" nav page (ScanLogPage); loads on open, no
// manual refresh button.
import { useState, useEffect, useCallback, useMemo } from 'react';
import Box from '@cloudscape-design/components/box';
import Divider from '@cloudscape-design/components/divider';
import Select, { SelectProps } from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Steps, { StepsProps } from '@cloudscape-design/components/steps';
import StatusIndicator, { StatusIndicatorProps } from '@cloudscape-design/components/status-indicator';
import { getRecentScans, getRecentMigrationRuns, ScanRecord, MigrationBatchRecord } from '../api';
import { migrationSummaryLine } from '../constants';

// Internal scan "cells" are named <account>-<region>, and the hub account's cell
// uses the literal "hub". Turn that into something a user understands: "hub"
// becomes "this account", and the -<region> suffix is shown in parentheses.
// A real 12-digit account id is left as-is (optionally with its region).
const humanizeCell = (cell: string): string => {
  const m = cell.match(/^(hub|\d{12})(?:-(.+))?$/);
  if (!m) return cell;
  const who = m[1] === 'hub' ? 'this account' : m[1];
  return m[2] ? `${who} (${m[2]})` : who;
};

// --- Day grouping (Today / Yesterday / absolute date) ----------------------

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

const dayLabel = (d: Date): string => {
  const today = startOfDay(new Date());
  const that = startOfDay(d);
  const diffDays = Math.round((today.getTime() - that.getTime()) / 86400000);
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
};

const timeLabel = (d: Date): string =>
  d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' });

// --- Scan status mapping ---------------------------------------------------
// Each scan maps to a Steps entry. The pattern uses status 'log'; we vary the
// icon aria-label by run outcome so SUCCEEDED / FAILED read differently.

type Outcome = { stepStatus: StepsProps.Step['status']; iconLabel: string };

const outcomeFor = (scan: ScanRecord): Outcome => {
  switch (scan.status) {
    case 'SUCCEEDED':
      return { stepStatus: 'success', iconLabel: 'Discover succeeded' };
    case 'FAILED':
    case 'TIMED_OUT':
      return { stepStatus: 'error', iconLabel: 'Discover failed' };
    case 'STOPPED':
      return { stepStatus: 'stopped', iconLabel: 'Discover stopped' };
    case 'RUNNING':
    default:
      return { stepStatus: 'loading', iconLabel: 'Discover running' };
  }
};

// Past-tense, action-oriented label describing what the Discover run did or skipped.
const scanHeader = (scan: ScanRecord): string => {
  if (scan.status === 'RUNNING') return 'Started Discover';
  if (scan.status === 'STOPPED') return 'Discover stopped before it finished';
  if (scan.status === 'FAILED' || scan.status === 'TIMED_OUT') return 'Discover failed';

  const disc = scan.summary?.discover;
  const asmt = scan.summary?.assess;
  if (!disc) return 'Completed Discover';

  const scanned = disc.accounts_scanned?.length ?? disc.cells_succeeded ?? 0;
  const discovered = disc.discovered ?? 0;
  const acctWord = scanned === 1 ? 'account' : 'accounts';
  const fnWord = discovered === 1 ? 'function' : 'functions';
  let header = `Scanned ${scanned} ${acctWord}, discovered ${discovered} deprecated-runtime ${fnWord}`;
  if (asmt?.assessed) {
    const aWord = asmt.assessed === 1 ? 'function' : 'functions';
    header += `, assessed ${asmt.assessed} ${aWord}`;
  }
  return header;
};

// Secondary line: accounts skipped / failed with reasons, when the summary has them.
const scanDetail = (scan: ScanRecord): string | undefined => {
  const disc = scan.summary?.discover;
  if (!disc) return undefined;
  const parts: string[] = [];
  if (disc.accounts_failed?.length) {
    parts.push(`Skipped ${disc.accounts_failed.length} account(s): ${disc.accounts_failed.join(', ')}`);
  } else if (disc.failed_cells?.length) {
    parts.push(`Skipped ${disc.failed_cells.length} scope(s): ${disc.failed_cells.map(humanizeCell).join(', ')}`);
  }
  if (scan.summary?.assess?.skipped) {
    parts.push(`${scan.summary.assess.skipped} function(s) skipped during assessment`);
  }
  return parts.length ? parts.join('. ') : undefined;
};

// --- Migration batch mapping -----------------------------------------------
// One entry per batch execution. A SUCCEEDED batch carries the durable Result
// (migrate.summarize_migration) and reads as its one-liner
// ("2 selected, 1 rejected, 1 branch ready" — constants.migrationSummaryLine,
// the same text the My functions card header and the finish flash use).

type MigrationOutcome = { type: StatusIndicatorProps.Type; text: string };

const migrationOutcome = (run: MigrationBatchRecord): MigrationOutcome => {
  switch (run.status) {
    case 'RUNNING':
      return { type: 'in-progress', text: 'Started a migration' };
    case 'SUCCEEDED':
      return {
        // A batch with failed units is a mixed outcome, not a clean success.
        type: run.summary?.failed ? 'warning' : 'success',
        text: run.summary ? migrationSummaryLine(run.summary) : 'Completed a migration',
      };
    case 'FAILED':
    case 'TIMED_OUT':
      return { type: 'error', text: 'Migration batch failed' };
    case 'STOPPED':
    default:
      return { type: 'stopped', text: 'Migration batch stopped before it finished' };
  }
};

// --- Unified timeline entry ------------------------------------------------

type Entry =
  | { kind: 'scan'; scan: ScanRecord; when?: Date }
  | { kind: 'migration'; run: MigrationBatchRecord; when?: Date };

const entryWhen = (e: Entry): Date | undefined =>
  e.kind === 'scan' ? e.scan.startedAt : e.run.startedAt;

// --- Grouping into day buckets, newest first -------------------------------

interface EntryGroup {
  label: string;
  entries: Entry[];
}

const groupByDay = (entries: Entry[]): EntryGroup[] => {
  const groups: EntryGroup[] = [];
  for (const entry of entries) {
    const when = entryWhen(entry);
    const label = when ? dayLabel(when) : 'Earlier';
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.entries.push(entry);
    else groups.push({ label, entries: [entry] });
  }
  return groups;
};

// Map a Steps status to a StatusIndicator type for the inline scan outcome icon.
const stepTypeToIndicator = (s: StepsProps.Step['status']): StatusIndicatorProps.Type => {
  switch (s) {
    case 'success': return 'success';
    case 'error': return 'error';
    case 'stopped': return 'stopped';
    case 'loading': return 'in-progress';
    default: return 'info';
  }
};

// Render one timeline entry as a Steps step: ONE compact line per event, in the
// Cloudscape event-log layout, where the
// timestamp is the `annotation` and the whole message is the `header`, kept
// inline. Two things previously broke the one-liner and are deliberately
// avoided here: a SpaceBetween/Badge wrapper in the header (a block container
// that pushes the text under the timestamp) and a separate `details` slot (an
// indented second line). Instead the kind ("Scan" / "Migration") is a short
// text prefix and any secondary detail is folded into the same line.
const renderStep = (entry: Entry): StepsProps.Step => {
  const when = entryWhen(entry);
  const annotation = when ? timeLabel(when) : '';

  if (entry.kind === 'scan') {
    const scan = entry.scan;
    const outcome = outcomeFor(scan);
    const detail = scanDetail(scan);
    const text = detail ? `${scanHeader(scan)} · ${detail}` : scanHeader(scan);
    return {
      status: 'log',
      statusIconAriaLabel: outcome.iconLabel,
      annotation,
      header: (
        <StatusIndicator type={stepTypeToIndicator(outcome.stepStatus)}>{`Discover · ${text}`}</StatusIndicator>
      ),
    } as StepsProps.Step;
  }

  // No link in the log: the branch link lives in the My functions table.
  const outcome = migrationOutcome(entry.run);
  return {
    status: 'log',
    statusIconAriaLabel: `Migration ${entry.run.status.toLowerCase()}`,
    annotation,
    header: (
      <StatusIndicator type={outcome.type}>{`Migration · ${outcome.text}`}</StatusIndicator>
    ),
  } as StepsProps.Step;
};

// --- Component -------------------------------------------------------------

const FILTER_OPTIONS: SelectProps.Options = [
  { value: 'all', label: 'All activity' },
  { value: 'Today', label: 'Today' },
  { value: 'Yesterday', label: 'Yesterday' },
];

export default function ScanLog() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<SelectProps.Option>(FILTER_OPTIONS[0] as SelectProps.Option);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [scans, runs] = await Promise.all([getRecentScans(10), getRecentMigrationRuns(10)]);
      const merged: Entry[] = [
        ...scans.map((scan) => ({ kind: 'scan' as const, scan, when: scan.startedAt })),
        ...runs.map((run) => ({ kind: 'migration' as const, run, when: run.startedAt })),
      ];
      // Newest first; entries without a timestamp sort last.
      merged.sort((a, b) => (b.when?.getTime() ?? 0) - (a.when?.getTime() ?? 0));
      setEntries(merged);
    } catch (err: any) {
      setError(err.message || 'Could not load the activity log');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const visible = useMemo(
    () => (filter.value === 'all'
      ? entries
      : entries.filter((e) => { const w = entryWhen(e); return w && dayLabel(w) === filter.value; })),
    [entries, filter],
  );
  const groups = useMemo(() => groupByDay(visible), [visible]);

  return (
    <SpaceBetween size="l">
      <Select
        selectedOption={filter}
        onChange={({ detail }) => setFilter(detail.selectedOption)}
        options={FILTER_OPTIONS}
        inlineLabelText="Filter"
        disabled={loading || !!error}
      />
      {loading && entries.length > 0 && <StatusIndicator type="loading">Refreshing</StatusIndicator>}

      {error && <StatusIndicator type="error">{error}</StatusIndicator>}

      {!error && !loading && entries.length === 0 && (
        <Box color="text-body-secondary">No activity recorded yet. Run Discover or migrate functions to populate the log.</Box>
      )}

      {!error && loading && entries.length === 0 && (
        <StatusIndicator type="loading">Loading the activity log</StatusIndicator>
      )}

      {groups.map((group) => (
        <SpaceBetween key={group.label} size="s">
          <Divider>{group.label}</Divider>
          <Steps
            ariaLabel={`${group.label} activity`}
            steps={group.entries.map(renderStep)}
          />
        </SpaceBetween>
      ))}
    </SpaceBetween>
  );
}
