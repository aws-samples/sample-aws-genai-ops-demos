// Home = the org-wide inventory of deprecated-runtime Lambda functions.
// Inventory-first: a thin labelled summary strip over a PropertyFilter table.
// "Discover" runs the whole org and starts/polls the durable
// Discover + Assess run; the operator narrows the results in the table's
// PropertyFilter (region, tags), ticks the rows to migrate, and clicks
// "Migrate (N)": one batch durable execution resolves each repository, runs AWS
// Transform in CodeBuild and pushes the upgrade branch; the row ends as "Branch
// ready" with a link to the branch. Row click opens a drawer with the Assess
// facts + the scan Steps. Modeled on aws-services-lifecycle-tracker/MyResources.tsx.
import { useState, useEffect, useMemo, useRef, useCallback, ReactNode } from 'react';
import Table, { TableProps } from '@cloudscape-design/components/table';
import Header from '@cloudscape-design/components/header';
import Box from '@cloudscape-design/components/box';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Container from '@cloudscape-design/components/container';
import KeyValuePairs, { KeyValuePairsProps } from '@cloudscape-design/components/key-value-pairs';
import Icon from '@cloudscape-design/components/icon';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Popover from '@cloudscape-design/components/popover';
import PropertyFilter, { PropertyFilterProps } from '@cloudscape-design/components/property-filter';
import CollectionPreferences, { CollectionPreferencesProps } from '@cloudscape-design/components/collection-preferences';
import Pagination from '@cloudscape-design/components/pagination';
import { useCollection } from '@cloudscape-design/collection-hooks';
import Button from '@cloudscape-design/components/button';
import Toggle from '@cloudscape-design/components/toggle';
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar';
import Skeleton from '@cloudscape-design/components/skeleton';
import Alert from '@cloudscape-design/components/alert';
import { useNavigate } from 'react-router-dom';
import { getFunctions, getSettings, startScan, getScanStatus, getRecentScans, getRecentMigrationRuns, startMigrationBatch, getMigrationBatchStatus, MigrationFunction, MigrationRunStatus, ScanPhase, RefreshExecutionStatus, MigrationBatchStatus, ScanSummary } from '../api';
import { MIGRATION_RUN_STATUS, PRIORITY_INDICATOR, ALERT_STATUS_LABEL, DEFAULT_REPO_TAG, migrationRunConfig, failureCodeHelp, migrationSummaryLine } from '../constants';
import { funcName, lambdaConsoleUrl, codecommitBranchUrl, repoNameFromCloneUrl } from '../console-links';
import { tagKeys, tagValues, matchesTags } from '../tag-filter';
import { deprecationState, isActiveBacklog, tallyMigrationRuns } from '../migration-tallies';
import { useSplitPanel } from '../SplitPanelContext';
import Link from '@cloudscape-design/components/link';
import ScanSteps from '../components/ScanSteps';
import MigrationSteps from '../components/MigrationSteps';

// Polling cadences: a scan is short-lived, a migration batch runs for minutes;
// the first tick after a click comes quickly so the card fills in at once.
const SCAN_POLL_MS = 3000;
const MIGRATION_POLL_MS = 30000;
const FIRST_TICK_MS = 1000;

const errorMessage = (err: unknown): string => (err instanceof Error ? err.message : String(err));

const URGENCY_RANK: Record<string, number> = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3, INACTIVE: 4 };
const urgencyRank = (f: MigrationFunction) => URGENCY_RANK[f.priority_label || ''] ?? 4;
// Backend priority labels are UPPERCASE (CRITICAL/HIGH/...), used as data keys
// above. Display them in sentence case to match the rest of the UI (Deprecated,
// Not started, Migrated) — "CRITICAL" -> "Critical".
const titleCase = (s: string): string => s ? s.charAt(0).toUpperCase() + s.slice(1).toLowerCase() : s;

// Default sort: urgency descending, tiebreak days-to-deprecation ascending.
const urgencySort = (a: MigrationFunction, b: MigrationFunction) =>
  urgencyRank(a) - urgencyRank(b)
  || (a.days_to_deprecation ?? Number.MAX_SAFE_INTEGER) - (b.days_to_deprecation ?? Number.MAX_SAFE_INTEGER);

const PREFS_KEY = 'lambda-migration-functions-preferences';
const DEFAULT_PREFS: CollectionPreferencesProps.Preferences = {
  pageSize: 50,
  contentDisplay: ['function', 'accountId', 'accountName', 'region', 'runtime', 'deprecation', 'urgency', 'assessment', 'migration'].map((id) => ({ id, visible: true })),
};
const loadPrefs = (): CollectionPreferencesProps.Preferences => {
  try { return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') }; } catch { return DEFAULT_PREFS; }
};

const TAG_PROP = 'tag:';
const haystack = (r: MigrationFunction) =>
  `${funcName(r.function_arn)} ${r.runtime} ${r.region} ${r.account_id || ''} ${r.account_name || ''} ${r.migration_status || ''}`.toLowerCase();
const matchToken = (r: MigrationFunction, t: PropertyFilterProps.Query['tokens'][number]): boolean => {
  const v = String(t.value ?? '').toLowerCase();
  if (!t.propertyKey) return t.operator === '!:' ? !haystack(r).includes(v) : haystack(r).includes(v);
  if (t.propertyKey.startsWith(TAG_PROP)) {
    const key = t.propertyKey.slice(TAG_PROP.length);
    const has = matchesTags(r.tags, [{ key, values: [String(t.value ?? '')] }]);
    return t.operator === '!=' ? !has : has;
  }
  // A row with no mig# item is NOT_STARTED (the same default the column renders).
  const raw = t.propertyKey === 'migration_run_status'
    ? (r.migration_run_status || 'NOT_STARTED')
    : (r as any)[t.propertyKey];
  const field = String(raw ?? '').toLowerCase();
  return t.operator === '!=' ? field !== v : field === v;
};
const matchQuery = (r: MigrationFunction, q: PropertyFilterProps.Query): boolean =>
  q.tokens.length === 0 || (q.operation === 'or' ? q.tokens.some((t) => matchToken(r, t)) : q.tokens.every((t) => matchToken(r, t)));

const FILTER_I18N: PropertyFilterProps.I18nStrings = {
  filteringAriaLabel: 'Filter functions',
  filteringPlaceholder: 'Search functions, or filter by region, account, tag',
  clearFiltersText: 'Clear filters',
  operationAndText: 'and', operationOrText: 'or',
  operatorText: 'Operator', operatorsText: 'Operators',
  operatorEqualsText: 'equals', operatorDoesNotEqualText: 'does not equal',
  operatorContainsText: 'contains', operatorDoesNotContainText: 'does not contain',
  propertyText: 'Property', valueText: 'Value', cancelActionText: 'Cancel', applyActionText: 'Apply',
  allPropertiesLabel: 'All properties', groupValuesText: 'Values', groupPropertiesText: 'Properties',
  tokenLimitShowMore: 'Show more', tokenLimitShowFewer: 'Show fewer',
  editTokenHeader: 'Edit filter', dismissAriaLabel: 'Remove filter', enteredTextLabel: (t) => `Search "${t}"`,
  removeTokenButtonAriaLabel: (t) => `Remove ${t.propertyKey || 'search'} ${t.operator} ${t.value}`,
};

// TA alert_status is stored title-case (Red/Yellow/Green); the PropertyFilter
// token shows its meaning (ALERT_STATUS_LABEL), not the raw colour.
const alertStatusLabel = (value: string): string => ALERT_STATUS_LABEL[value] ?? value;
// Migration-run and complexity tokens read the same labels as the columns
// ("Branch ready", "Rejected"; "Low", "High"), not the raw enum.
const migrationRunLabel = (value: string): string => migrationRunConfig(value).label;
const complexityLabel = (value: string): string => titleCase(value);

// The single Migration axis. An active/terminal mig# run (the Phase-2 lifecycle
// enum) wins; otherwise the Phase-1 inferred-MIGRATED state (set when Trusted
// Advisor stops reporting the function) folds in as a success here, else "Not
// started". Labels and indicator types come from constants.MIGRATION_RUN_STATUS
// (shared with the How it works page); BRANCH_READY carries a link to the
// pushed upgrade branch in the CodeCommit console, REJECTED (neutral: triaged
// out before any build, e.g. no repo tag) and FAILED (error: transform / push /
// timeout) both show the failure code help and detail. NO_CHANGES (AWS
// Transform found nothing to change) is neutral.
// The raw value is kept as a string: a row may carry a status this build does
// not know, and migrationRunConfig renders those with the raw value.
const migrationRunState = (f: MigrationFunction): string => f.migration_run_status || 'NOT_STARTED';

// Popover / drawer text for a rejection or failure: "<label> — <detail> · <next step>".
const failureText = (f: MigrationFunction): string => {
  const help = failureCodeHelp(f.migration_failure_code);
  const head = help?.label ?? f.migration_failure_code ?? 'Failed';
  const detail = f.migration_failure_detail ?? help?.meaning;
  return `${head}${detail ? ` — ${detail}` : ''}${help ? ` · ${help.next}` : ''}`;
};

// The pushed upgrade branch in the CodeCommit console (null until BRANCH_READY).
const branchUrl = (f: MigrationFunction): string | null =>
  migrationRunState(f) === 'BRANCH_READY'
    ? codecommitBranchUrl(f.region, repoNameFromCloneUrl(f.migration_repo), f.migration_branch || '')
    : null;
// Skeleton cell per column for the table's first load. Mirrors the settled
// content's shape: a status column is an icon dot plus a short label; an id or
// region is a fixed-width token; the function name takes the whole cell.
// Must stay non-interactive: the Table renders these inside aria-hidden rows.
const statusSkeleton = (label: string) => (
  <SpaceBetween direction="horizontal" size="xs">
    <Skeleton width="16px" />
    <Skeleton width={label} />
  </SpaceBetween>
);
const renderSkeletonCell = (column: TableProps.ColumnDefinition<MigrationFunction>) => {
  switch (column.id) {
    case 'accountId': return <Skeleton width="12ch" />;
    case 'accountName': return <Skeleton width="60%" />;
    case 'region':
    case 'runtime': return <Skeleton width="9ch" />;
    case 'deprecation': return statusSkeleton('8ch');
    case 'urgency': return statusSkeleton('6ch');
    case 'assessment':
    case 'migration': return statusSkeleton('9ch');
    default: return undefined;   // 'function': default full-width line
  }
};

const renderMigrationRun = (f: MigrationFunction): JSX.Element => {
  const state = migrationRunState(f);
  const cfg = migrationRunConfig(state);
  switch (state) {
    case 'QUEUED':
    case 'IN_PROGRESS':
      return <StatusIndicator type="in-progress">{cfg.label}</StatusIndicator>;
    case 'BRANCH_READY': {
      // The pushed upgrade branch IS the deliverable: link straight to it.
      const url = branchUrl(f);
      return (
        <StatusIndicator type="success">
          {url ? <Link external href={url}>{cfg.label}</Link> : cfg.label}
        </StatusIndicator>
      );
    }
    case 'NO_CHANGES':
      return <StatusIndicator type="info">{cfg.label}</StatusIndicator>;
    case 'REJECTED':
    case 'FAILED': {
      // Rejected is neutral (stopped): nothing was transformed, so it must not
      // read as an error the way a real transform / push failure does.
      const indicator = (
        <StatusIndicator type={state === 'REJECTED' ? 'stopped' : 'error'}>{cfg.label}</StatusIndicator>
      );
      return f.migration_failure_code || f.migration_failure_detail
        ? <Popover dismissButton={false} position="top" size="medium" triggerType="text" content={failureText(f)}>{indicator}</Popover>
        : indicator;
    }
    case 'NOT_STARTED':
      // No active/terminal mig# run: fold in the Phase-1 inferred-migrated
      // state (migration_status === 'MIGRATED'), otherwise "Not started".
      return f.migration_status === 'MIGRATED'
        ? <StatusIndicator type="success">Migrated</StatusIndicator>
        : <StatusIndicator type="not-started">{cfg.label}</StatusIndicator>;
    default:
      // Unknown-value guard: a status this build does not know renders with
      // its raw value and never throws.
      return <StatusIndicator type="pending">{cfg.label}</StatusIndicator>;
  }
};

// Properties card for the drawer: every identity fact the fn# row carries,
// grouped like the hero KeyValuePairs. A pair is emitted only when it has a
// value (arrays: non-empty); the three deprecation facts keep an em-dash
// placeholder because they are the card's always-shown facts. Groups with no
// data are dropped entirely so the card stays compact.
const dash = <Box color="text-body-secondary">—</Box>;
const hasValue = (v: unknown): boolean => v != null && v !== '' && !(Array.isArray(v) && v.length === 0);
const pair = (label: string, value: unknown, render?: (v: any) => ReactNode): KeyValuePairsProps.Pair[] =>
  hasValue(value) ? [{ label, value: render ? render(value) : (value as ReactNode) }] : [];
const group = (title: string, items: KeyValuePairsProps.Pair[]): KeyValuePairsProps.Group[] =>
  items.length > 0 ? [{ type: 'group', title, items }] : [];

const propertyItems = (f: MigrationFunction): KeyValuePairsProps.Item[] => {
  const st = deprecationState(f);
  const tags = Object.entries(f.tags || {}).sort(([a], [b]) => a.localeCompare(b));
  return [
    ...group('Deprecation', [
      { label: 'State', value: st ? <StatusIndicator type={st.type}>{st.label}</StatusIndicator> : dash },
      { label: 'Deprecation date', value: f.deprecation_date || dash },
      {
        label: 'Days to deprecation',
        value: f.days_to_deprecation != null
          ? (f.days_to_deprecation < 0 ? `${Math.abs(f.days_to_deprecation)} days past EOL` : `${f.days_to_deprecation} days`)
          : dash,
      },
      ...pair('Migrated', f.resolved_at, (v) => (
        <Box variant="small" color="text-body-secondary">{`Inferred — no longer reported by Trusted Advisor as of ${v}`}</Box>
      )),
    ]),
    ...group('Runtime', [
      ...pair('Runtime', f.runtime),
      ...pair('Package type', f.package_type),
      ...pair('Handler', f.handler, (v) => <Box variant="code">{v}</Box>),
      ...(f.layers?.length ? [{
        label: `Layers (${f.layers.length})`,
        value: <SpaceBetween size="xxxs">{f.layers.map((l, i) => <Box key={i} variant="code">{l}</Box>)}</SpaceBetween>,
      }] : []),
      ...pair('Memory', f.memory_size, (v) => `${Number(v).toLocaleString()} MB`),
      ...pair('Timeout', f.timeout, (v) => `${v} s`),
    ]),
    ...group('Usage', [
      ...pair('Average daily invocations', f.avg_daily_invokes, (v) => Number(v).toLocaleString()),
      ...pair('Invocations (14 days)', f.invocation_count_14d, (v) => Number(v).toLocaleString()),
    ]),
    ...group('Code', [
      ...pair('Lines of code', f.lines_of_code, (v) => Number(v).toLocaleString()),
      ...pair('Source files', f.num_source_files, (v) => String(v)),
    ]),
    ...group('Priority', f.priority_label ? [{
      label: 'Priority',
      value: (
        <SpaceBetween size="xxxs">
          <StatusIndicator type={PRIORITY_INDICATOR[f.priority_label] || 'pending'}>{titleCase(f.priority_label)}</StatusIndicator>
          {f.priority_score != null && (
            <Box variant="small">
              {f.priority_base != null && f.priority_adjustment != null
                ? `Score ${f.priority_score} (base ${f.priority_base}, ${f.priority_adjustment >= 0 ? '+' : ''}${f.priority_adjustment})`
                : `Score ${f.priority_score}`}
            </Box>
          )}
          {f.priority_reasoning && <Box variant="small" color="text-body-secondary">{f.priority_reasoning}</Box>}
        </SpaceBetween>
      ),
    }] : []),
    ...group('Identity', [
      ...pair('Account', f.account_name && f.account_id ? `${f.account_name} (${f.account_id})` : (f.account_name || f.account_id)),
      ...pair('Region', f.region),
      ...pair('First seen', f.first_seen_at),
    ]),
    ...group('Tags', tags.map(([k, v]) => ({ label: k, value: <Box variant="code">{v}</Box> }))),
  ];
};

// Drawer: identity card + Assess facts for one function + the rescan Steps when a run is known.
function FunctionDrawer({ f, phases, running }: { f: MigrationFunction; phases: ScanPhase[] | null; running: boolean }) {
  const consoleUrl = lambdaConsoleUrl(f);
  return (
    <SpaceBetween size="l">
      <Container
        header={
          // h2 on purpose: KeyValuePairs renders its group titles (Deprecation,
          // Runtime, ...) at h3, so the container header must sit one level
          // above them or the card reads as a flat list of same-size titles.
          // Same hierarchy as the "My functions" hero container.
          <Header
            variant="h2"
            actions={consoleUrl && (
              <Link external href={consoleUrl} ariaLabel={`Open ${funcName(f.function_arn)} in the Lambda console`}>
                Open in Lambda console
              </Link>
            )}
          >
            Properties
          </Header>
        }
      >
        {/* The function's identity card: deprecation dates (moved out of the
            table, which shows state only), runtime sizing, usage, code size,
            priority, account/region and tags — none of which belong in the table. */}
        <KeyValuePairs columns={3} items={propertyItems(f)} />
      </Container>
      <Container header={<Header variant="h2" info={<Icon name="gen-ai" size="small" ariaLabel="Generated by AI" />}>Assessment</Header>}>
        <KeyValuePairs
          columns={3}
          items={[
            { label: 'Complexity', value: f.migration_complexity ? <StatusIndicator type="success">{titleCase(f.migration_complexity)}</StatusIndicator> : <Box color="text-body-secondary">Not assessed</Box> },
          ]}
        />
        {f.source_truncated && <StatusIndicator type="warning">Assessment based on a truncated copy of the source</StatusIndicator>}
        {f.assessment_summary && <Box variant="p" padding={{ top: 's' }}>{f.assessment_summary}</Box>}
        {!!f.deprecated_apis?.length && (
          <ExpandableSection headerText={`Deprecated APIs (${f.deprecated_apis.length})`}>
            <ul>{f.deprecated_apis.map((x, i) => <li key={i}>{x}</li>)}</ul>
          </ExpandableSection>
        )}
        {!!f.breaking_changes?.length && (
          <ExpandableSection headerText={`Breaking changes (${f.breaking_changes.length})`}>
            <ul>{f.breaking_changes.map((x, i) => <li key={i}>{x}</li>)}</ul>
          </ExpandableSection>
        )}
        {!!f.dependency_issues?.length && (
          <ExpandableSection headerText={`Dependency issues (${f.dependency_issues.length})`}>
            <ul>{f.dependency_issues.map((x, i) => <li key={i}>{x}</li>)}</ul>
          </ExpandableSection>
        )}
      </Container>
      <Container header={<Header variant="h2">Migration</Header>}>
        <KeyValuePairs
          columns={3}
          items={[
            { label: 'Status', value: renderMigrationRun(f) },
            // The target the run actually used: the family target fixed by the
            // transformation (see backend/transform_runtimes.py).
            ...pair('Target runtime', f.migration_target, (v) => `${f.runtime} → ${v}`),
            // The upgrade branch is shown only on BRANCH_READY: a stale value
            // from an earlier run must never read as a pushed branch.
            ...(migrationRunState(f) === 'BRANCH_READY' ? pair('Branch', f.migration_branch, (v) => <Box variant="code">{v}</Box>) : []),
            ...(f.migration_failure_code || f.migration_failure_detail ? [{ label: 'Failure', value: <Box variant="small">{failureText(f)}</Box> }] : []),
            ...pair('Build', f.migration_build_id, (v) => <Box variant="code">{v}</Box>),
            ...pair('Updated', f.migration_run_updated_at),
            // One code line per `git diff --stat` line (Cloudscape has no
            // pre-wrap primitive and custom CSS is out of scope).
            ...pair('Diff summary', f.migration_diff_summary, (v: string) => (
              <SpaceBetween size="xxxs">
                {v.split('\n').filter((line) => line.trim()).map((line, i) => <Box key={i} variant="code">{line}</Box>)}
              </SpaceBetween>
            )),
          ]}
        />
      </Container>
      {phases && phases.length > 0 && <ScanSteps phases={phases} running={running} />}
    </SpaceBetween>
  );
}

export default function MyFunctions() {
  const { openPanel, closePanel } = useSplitPanel();
  const navigate = useNavigate();
  const [rows, setRows] = useState<MigrationFunction[]>([]);
  const [loading, setLoading] = useState(true);
  const [preferences, setPreferences] = useState(loadPrefs);
  const [selected, setSelected] = useState<MigrationFunction[]>([]);
  const [flashbarItems, setFlashbarItems] = useState<FlashbarProps.MessageDefinition[]>([]);
  // Default view is the ACTIVE backlog only (deprecated/approaching). Resolved
  // (inferred-green / migrated) functions are retained as history but hidden by
  // default so they don't pollute the working view; the toggle reveals them.
  const [includeResolved, setIncludeResolved] = useState(false);

  // Rescan (durable Discover + Assess) run state
  const [refreshArn, setRefreshArn] = useState<string | null>(null);
  const [refreshStatus, setRefreshStatus] = useState<RefreshExecutionStatus | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Migration run state. A Migrate click starts ONE batch durable execution over
  // the selected ARNs (no wizard). A single migrationRunArn drives the
  // MigrationSteps card and the 30 s poll of getMigrationBatchStatus. The card is
  // persistent: on page load the most recent batch is re-attached (collapsed),
  // a Migrate click expands it, and it auto-collapses ONCE per execution as soon
  // as nothing but builds is in flight (latched in autoCollapsedFor so a later
  // wave's resolve never re-expands it; only the user does).
  const [migrating, setMigrating] = useState(false);
  const [migrationRunArn, setMigrationRunArn] = useState<string | null>(null);
  const [migrationRun, setMigrationRun] = useState<MigrationBatchStatus | null>(null);
  const [stepsExpanded, setStepsExpanded] = useState(false);
  const migratePollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoCollapsedFor = useRef<string | null>(null);
  // The finish flash fires once, and only for a batch this page followed: one
  // started by this page's Migrate click (set before the first tick, so an
  // all-rejected batch that ends within the first delay still flashes) or one a
  // tick observed RUNNING. A page opened after the run ended never flashes.
  const flashEligibleFor = useRef<string | null>(null);
  const finishFlashedFor = useRef<string | null>(null);
  // Set by a Migrate click; the mount re-attach (loadLatestMigration) resolves
  // asynchronously and must not overwrite a batch started meanwhile.
  const startedHereFor = useRef<string | null>(null);

  // Latest scan summary, loaded from the scan log so the "not checked" counter
  // survives a page reload (not just the in-session run).
  const [latestSummary, setLatestSummary] = useState<ScanSummary | null>(null);

  const loadLatestSummary = useCallback(async () => {
    try {
      const [recent] = await getRecentScans(1);
      if (recent?.summary) setLatestSummary(recent.summary);
    } catch {
      // Non-fatal: the counter just stays hidden if the scan log can't be read.
    }
  }, []);

  // Re-attach the Steps card to the most recent migration batch (collapsed), so
  // navigating away and back, or reloading, never loses the last attempt.
  const loadLatestMigration = useCallback(async () => {
    try {
      const [latest] = await getRecentMigrationRuns(1);
      // A Migrate click since mount owns the card: ignore the (older) re-attach.
      if (!latest || startedHereFor.current) return;
      const run = await getMigrationBatchStatus(latest.executionArn);
      if (startedHereFor.current) return;
      setMigrationRunArn(latest.executionArn);
      setMigrationRun(run);
      setStepsExpanded(false);
    } catch (err) {
      // Non-fatal: the card is optional (no migration batch yet returns []).
      console.warn('Could not re-attach to the latest migration batch', err);
    }
  }, []);

  // Settings never saved? get_settings returns {} for an absent row (a saved row
  // always carries updated_at). Migrate then falls back to the default
  // DEFAULT_REPO_TAG tag, which is legitimate but must be SAID before the user
  // learns it from a wall of "Rejected: no repo tag". null = not known yet.
  const [settingsUnset, setSettingsUnset] = useState<boolean | null>(null);
  const [settingsNoticeDismissed, setSettingsNoticeDismissed] = useState(false);
  const loadSettingsState = useCallback(async () => {
    try {
      const { settings } = await getSettings();
      setSettingsUnset(Object.keys(settings).length === 0);
    } catch {
      setSettingsUnset(null);   // unknown: say nothing rather than something wrong
    }
  }, []);

  const flash = (type: FlashbarProps.Type, content: string) =>
    setFlashbarItems([{ type, content, dismissible: true, dismissLabel: 'Dismiss', onDismiss: () => setFlashbarItems([]), id: `${type}-${Date.now()}` }]);

  // Rows shown QUEUED right after Migrate, before the batch has written their
  // mig# item: function ARN -> when Migrate was clicked (ms). Until the server
  // reports a record written after the click, a reload would show the PREVIOUS
  // run's outcome (e.g. Branch ready) for a few seconds, so the optimistic QUEUED
  // is kept. Cleared per row once the server catches up, and entirely when the
  // batch execution is over.
  const optimisticQueued = useRef<Map<string, number>>(new Map());

  // Background reload: refresh the rows WITHOUT flipping the Table into its
  // loading state (the 30 s migration poll must not flicker the page).
  const reload = useCallback(async () => {
    try {
      const { functions } = await getFunctions();
      const pending = optimisticQueued.current;
      setRows(pending.size === 0 ? functions : functions.map((r) => {
        const clickedAt = pending.get(r.function_arn);
        if (clickedAt === undefined) return r;
        const live = r.migration_run_status === 'QUEUED' || r.migration_run_status === 'IN_PROGRESS';
        const updatedAt = r.migration_run_updated_at ? Date.parse(r.migration_run_updated_at) : NaN;
        if (live || (!Number.isNaN(updatedAt) && updatedAt >= clickedAt)) {
          pending.delete(r.function_arn);   // the server has this run's record
          return r;
        }
        return { ...r, migration_run_status: 'QUEUED' as MigrationRunStatus };
      }));
    } catch (err) {
      flash('error', `Failed to load functions: ${errorMessage(err)}`);
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      await reload();
    } finally {
      setLoading(false);
    }
  }, [reload]);

  useEffect(() => { load(); loadLatestSummary(); loadLatestMigration(); loadSettingsState(); }, [load, loadLatestSummary, loadLatestMigration, loadSettingsState]);

  // Poll an active rescan; reload the inventory when it finishes.
  const running = refreshStatus?.status === 'RUNNING';
  useEffect(() => {
    if (!refreshArn) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const status = await getScanStatus(refreshArn);
        if (cancelled) return;
        setRefreshStatus(status);
        if (status.status === 'RUNNING') {
          pollRef.current = setTimeout(tick, SCAN_POLL_MS);
        } else {
          flash(status.status === 'SUCCEEDED' ? 'success' : 'warning', `Discover ${status.status.toLowerCase()}`);
          if (status.summary) setLatestSummary(status.summary as ScanSummary);
          load();
        }
      } catch (err) {
        if (!cancelled) flash('error', `Could not read the Discover status: ${errorMessage(err)}`);
      }
    };
    pollRef.current = setTimeout(tick, FIRST_TICK_MS);
    return () => { cancelled = true; if (pollRef.current) clearTimeout(pollRef.current); };
  }, [refreshArn, load]);

  const rescan = async () => {
    try {
      const { executionArn, alreadyRunning } = await startScan();
      setRefreshArn(executionArn);
      if (alreadyRunning) flash('info', 'Discover is already running — re-attaching to it.');
    } catch (err) {
      flash('error', `Could not start Discover: ${errorMessage(err)}`);
    }
  };

  // Merge a freshly polled migration-run item onto its row (matched by ARN).
  const mergeMigrationRow = useCallback((arn: string, item: MigrationFunction) => {
    setRows((prev) => prev.map((r) => r.function_arn === arn
      ? {
        ...r,
        migration_run_status: item.migration_run_status || item.migration_status as MigrationRunStatus || 'NOT_STARTED',
        migration_failure_code: item.migration_failure_code,
        migration_failure_detail: item.migration_failure_detail,
        migration_branch: item.migration_branch,
        migration_repo: item.migration_repo,
        migration_diff_summary: item.migration_diff_summary,
        migration_build_id: item.migration_build_id,
        migration_target: item.migration_target,
        migration_run_updated_at: item.migration_run_updated_at,
      }
      : r));
  }, []);

  // Auto-collapse rule: ONCE per execution, as soon as nothing but builds is in
  // flight (prepare done, resolve settled, transform running or the execution
  // over). context.map runs at most 5 units at a time, so for a batch > 5 the
  // last builds cannot start until earlier ones finish; waiting for every build
  // would keep the card open for most of the run. The collapsed header reports
  // the running count instead.
  useEffect(() => {
    if (!migrationRun || !migrationRunArn) return;
    if (autoCollapsedFor.current === migrationRunArn) return;
    const [p0, p1, p2] = migrationRun.progress?.phases ?? [];
    if (!p0 || !p1 || !p2) return;
    const buildsOnly = p0.status === 'success'
      && !['pending', 'in-progress'].includes(p1.status)
      && (p2.status === 'in-progress' || migrationRun.status !== 'RUNNING');
    if (buildsOnly) {
      autoCollapsedFor.current = migrationRunArn;
      setStepsExpanded(false);
    }
  }, [migrationRun, migrationRunArn]);

  // ONE 30 s timer for the migration surface. It runs while the latest
  // batch execution is RUNNING, or while a visible row is QUEUED / IN_PROGRESS
  // and the execution state is not yet known to be terminal (an in-flight row
  // of a batch known to be over belongs to a dead writer: polling it forever
  // would never change anything). Each tick refreshes the card header and the
  // inventory rows in the background (no Table loading flicker). On the
  // RUNNING -> terminal transition: one more reload and ONE info flash.
  const migrationRunning = migrationRun?.status === 'RUNNING';
  const rowsInFlight = useMemo(
    () => rows.some((r) => isActiveBacklog(r) && (r.migration_run_status === 'QUEUED' || r.migration_run_status === 'IN_PROGRESS')),
    [rows],
  );
  const executionKnownTerminal = !!migrationRun && migrationRun.status !== 'RUNNING';
  const shouldPollMigration = migrationRunning || (rowsInFlight && !executionKnownTerminal);
  useEffect(() => {
    if (!shouldPollMigration) return;
    let cancelled = false;
    const tick = async () => {
      try {
        let run: MigrationBatchStatus | null = null;
        if (migrationRunArn) {
          run = await getMigrationBatchStatus(migrationRunArn);
          if (cancelled) return;
          setMigrationRun(run);
          if (run.status === 'RUNNING') flashEligibleFor.current = migrationRunArn;
          // The batch is over: every row's record is final, show it as is.
          else optimisticQueued.current.clear();
        }
        await reload();
        if (cancelled) return;
        if (run && run.status !== 'RUNNING'
            && flashEligibleFor.current === migrationRunArn
            && finishFlashedFor.current !== migrationRunArn) {
          finishFlashedFor.current = migrationRunArn;
          flash('info', `Migration batch finished: ${run.summary ? migrationSummaryLine(run.summary) : run.status.toLowerCase()}`);
          return;   // the dependency change stops the timer; nothing left to poll
        }
        if (!cancelled) migratePollRef.current = setTimeout(tick, MIGRATION_POLL_MS);
      } catch (err) {
        if (cancelled) return;
        // A transient read failure must not freeze the card and the rows while
        // the batch is still running: report it and keep the 30 s cadence.
        flash('error', `Could not read the migration status: ${errorMessage(err)}`);
        migratePollRef.current = setTimeout(tick, MIGRATION_POLL_MS);
      }
    };
    // First tick quickly after a click (the arn was just set), otherwise 30 s.
    migratePollRef.current = setTimeout(tick, migrationRun ? MIGRATION_POLL_MS : FIRST_TICK_MS);
    return () => { cancelled = true; if (migratePollRef.current) clearTimeout(migratePollRef.current); };
    // migrationRun is read only to pick the first delay; it is deliberately not
    // a dependency (every poll result would otherwise restart the timer).
  }, [shouldPollMigration, migrationRunArn, reload]);

  // Start ONE batch migration over the selected functions. Each function that
  // transforms cleanly ends with its upgrade branch pushed to the repository;
  // nothing is merged. At most ONE info flash, regardless of selection size.
  const migrate = async (fns: MigrationFunction[]) => {
    if (fns.length === 0) return;
    setMigrating(true);
    try {
      const { executionArn, batchSize, overCap } = await startMigrationBatch(fns.map((f) => f.function_arn));
      startedHereFor.current = executionArn;
      flashEligibleFor.current = executionArn;
      setMigrationRunArn(executionArn);
      setMigrationRun(null);
      setStepsExpanded(true);
      autoCollapsedFor.current = null;
      // Optimistic QUEUED on the started rows so the Migration column reacts now,
      // kept across reloads until the batch writes this run's record (see
      // optimisticQueued).
      const clickedAt = Date.now();
      for (const f of fns.slice(0, batchSize)) {
        optimisticQueued.current.set(f.function_arn, clickedAt);
        mergeMigrationRow(f.function_arn, { migration_run_status: 'QUEUED' } as MigrationFunction);
      }
      setSelected([]);
      const overCapNote = overCap > 0 ? `, ${overCap} not started (exceeds the per-run cap)` : '';
      const defaultsNote = settingsUnset ? ` Using the default ${DEFAULT_REPO_TAG} tag detection (Settings not configured).` : '';
      flash('info', `Migration started for ${batchSize} function${batchSize === 1 ? '' : 's'}${overCapNote}. The upgrade branch is pushed to each repository when AWS Transform produces changes.${defaultsNote}`);
    } catch (err) {
      flash('error', `Could not start the migration: ${errorMessage(err)}`);
    } finally {
      setMigrating(false);
    }
  };

  // Account columns/property only when the scan actually spans more than one
  // account. The hub's rows now carry its real account id (resolved for display
  // by the API), so "has an account id" is no longer the single-vs-multi signal
  // — count DISTINCT account ids instead, so a single-account scan stays single.
  const multipleAccounts = useMemo(() => new Set(rows.map((r) => r.account_id).filter(Boolean)).size > 1, [rows]);
  const accounts = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of rows) if (r.account_id) m.set(r.account_id, r.account_name || r.account_id);
    return [...m.entries()].map(([id, name]) => ({ id, name }));
  }, [rows]);
  const regions = useMemo(() => [...new Set(rows.map((r) => r.region).filter(Boolean))].sort(), [rows]);
  const tagKeyInfos = useMemo(() => tagKeys(rows).slice(0, 8), [rows]);

  // The toggled set drives BOTH the table and the summary strip, so the counters
  // always describe the rows the user can see. Resolved (inferred-green /
  // migrated) rows only enter either when "Include migrated" is on.
  const visibleRows = useMemo(
    () => (includeResolved ? rows : rows.filter(isActiveBacklog)),
    [rows, includeResolved],
  );
  const sorted = useMemo(() => [...visibleRows].sort(urgencySort), [visibleRows]);
  const resolvedCount = useMemo(() => rows.filter((r) => !isActiveBacklog(r)).length, [rows]);

  const runtimes = useMemo(() => [...new Set(rows.map((r) => r.runtime).filter(Boolean))].sort(), [rows]);
  // Migration-run statuses present in the rows (a row without a mig# item is
  // NOT_STARTED), in the enum's display order; unknown values go last.
  const runStatuses = useMemo(() => {
    const present = new Set(rows.map((r) => r.migration_run_status || 'NOT_STARTED'));
    const known = (Object.keys(MIGRATION_RUN_STATUS) as string[]).filter((s) => present.has(s as MigrationRunStatus));
    const unknown = [...present].filter((s) => !(s in MIGRATION_RUN_STATUS)).sort();
    return [...known, ...unknown];
  }, [rows]);
  const complexities = useMemo(() => {
    const order = ['LOW', 'MEDIUM', 'HIGH'];
    const present = [...new Set(rows.map((r) => r.migration_complexity).filter((c): c is string => !!c))];
    return present.sort((a, b) => order.indexOf(a.toUpperCase()) - order.indexOf(b.toUpperCase()));
  }, [rows]);
  const filteringProperties: PropertyFilterProps.FilteringProperty[] = useMemo(() => [
    { key: 'region', propertyLabel: 'Region', groupValuesLabel: 'Regions', operators: ['=', '!='] },
    { key: 'runtime', propertyLabel: 'Runtime', groupValuesLabel: 'Runtimes', operators: ['=', '!='] },
    // Deprecation status, from the Trusted Advisor alert_status (Red/Yellow/Green).
    // Per the PropertyFilter docs, labelled options (= / !=) need a matching
    // `format` on the operators so the token renders the human label, not the
    // raw colour value.
    { key: 'alert_status', propertyLabel: 'Status', groupValuesLabel: 'Deprecation status',
      operators: ['=', '!='].map((operator) => ({ operator, format: alertStatusLabel })) },
    { key: 'migration_run_status', propertyLabel: 'Migration', groupValuesLabel: 'Migration statuses',
      operators: ['=', '!='].map((operator) => ({ operator, format: migrationRunLabel })) },
    { key: 'migration_complexity', propertyLabel: 'Complexity', groupValuesLabel: 'Complexities',
      operators: ['=', '!='].map((operator) => ({ operator, format: complexityLabel })) },
    ...(multipleAccounts ? [{ key: 'account_id', propertyLabel: 'Account', groupValuesLabel: 'Accounts', operators: ['=', '!='] } as PropertyFilterProps.FilteringProperty] : []),
    ...tagKeyInfos.map((k) => ({ key: `${TAG_PROP}${k.key}`, propertyLabel: `Tag ${k.key}`, groupValuesLabel: `${k.key} values`, operators: ['=', '!='], group: 'tags' } as PropertyFilterProps.FilteringProperty)),
  ], [multipleAccounts, tagKeyInfos]);
  const filteringOptions: PropertyFilterProps.FilteringOption[] = useMemo(() => [
    ...regions.map((v) => ({ propertyKey: 'region', value: v })),
    ...runtimes.map((v) => ({ propertyKey: 'runtime', value: v })),
    // Labelled so the operator reads intent, not the raw TA colour.
    { propertyKey: 'alert_status', value: 'Red', label: 'Already deprecated (past EOL)' },
    { propertyKey: 'alert_status', value: 'Yellow', label: 'Approaching deprecation' },
    { propertyKey: 'alert_status', value: 'Green', label: 'Migrated' },
    ...runStatuses.map((v) => ({ propertyKey: 'migration_run_status', value: v, label: migrationRunLabel(v) })),
    ...complexities.map((v) => ({ propertyKey: 'migration_complexity', value: v, label: complexityLabel(v) })),
    ...(multipleAccounts ? accounts.map((a) => ({ propertyKey: 'account_id', value: a.id, label: a.name })) : []),
    ...tagKeyInfos.flatMap((k) => tagValues(rows, k.key).map((v) => ({ propertyKey: `${TAG_PROP}${k.key}`, value: v.value, label: `${v.value} (${v.resources})` }))),
  ], [rows, regions, runtimes, runStatuses, complexities, accounts, multipleAccounts, tagKeyInfos]);

  const { items, filteredItemsCount, collectionProps, propertyFilterProps, paginationProps } = useCollection(sorted, {
    propertyFiltering: { filteringProperties, filteringFunction: matchQuery },
    sorting: {},
    pagination: { pageSize: preferences.pageSize },
  });

  // ---- summary strip facts (the toggled set, same as the table) ----
  // Deprecation and Priority only ever count the active backlog, so the toggle
  // does not move them. The Migration group does move: Migrated lives on
  // resolved rows, which are hidden unless "Include migrated" is on, and the
  // counters must agree with what the table shows.
  // The Deprecation and Migration counts come from the shared tally helper
  // (migration-tallies.ts) the Statistics page reads too, so the two pages can
  // never disagree; only the Priority / runtime facts are computed here.
  const summary = useMemo(() => {
    const tallies = tallyMigrationRuns(visibleRows);
    const byRuntime = new Map<string, number>();
    const urgency = { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0, INACTIVE: 0 } as Record<string, number>;
    for (const r of visibleRows) {
      if (!isActiveBacklog(r)) continue;
      byRuntime.set(r.runtime, (byRuntime.get(r.runtime) || 0) + 1);
      if (r.priority_label && r.priority_label in urgency) urgency[r.priority_label] += 1;
    }
    const topRuntimes = [...byRuntime.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3)
      .map(([rt, n]) => `${rt} ×${n}`).join(' · ') || '—';
    return { ...tallies, urgency, topRuntimes };
  }, [visibleRows]);

  // Row drawer (split panel)
  const openDrawer = (f: MigrationFunction) => {
    const phases = refreshStatus?.progress?.phases ?? null;
    openPanel(funcName(f.function_arn), <FunctionDrawer f={f} phases={phases} running={running} />);
  };

  useEffect(() => () => closePanel(), []);

  const shown = filteredItemsCount ?? sorted.length;
  // First load = nothing on screen yet. Drives the skeletons (headline, every
  // KPI, table rows); later reloads keep the real data visible while fetching.
  const firstLoad = loading && rows.length === 0;
  // One rule for every KPI value in the hero container: a skeleton of roughly
  // the settled width on first load, the real value afterwards. Status KPIs
  // are an icon dot plus a short number; text KPIs pass their own width.
  const kpi = (value: ReactNode, width = '4ch'): ReactNode =>
    firstLoad ? <Skeleton width={width} /> : value;
  const inScopeDetail = `across ${multipleAccounts ? accounts.length : 1} ${multipleAccounts && accounts.length !== 1 ? 'accounts' : 'account'}, ${regions.length} ${regions.length === 1 ? 'region' : 'regions'}`;
  // Accounts skipped because their Support tier (Basic/Developer) is ineligible
  // for Trusted Advisor. A COUNTER, never a list — the list could be huge.
  const notChecked = latestSummary?.accounts_not_checked?.length ?? 0;

  return (
    <SpaceBetween size="l">
      <Flashbar items={flashbarItems} stackItems />

      {/* Settings never saved: say it here, before a Migrate, not through a
          column of "Rejected: no repo tag". Scan is unaffected (and says so by
          scoping the sentence to Migrate). Dismissible for the session; gone
          for good once a settings row exists. */}
      {settingsUnset === true && !settingsNoticeDismissed && (
        <Alert
          type="info"
          dismissible
          onDismiss={() => setSettingsNoticeDismissed(true)}
          header="Source repository detection is not configured"
          action={<Button onClick={() => navigate('/settings')}>Open Settings</Button>}
        >
          Migrate will look for a <Box variant="code" display="inline">{DEFAULT_REPO_TAG}</Box> tag on each function
          to find its CodeCommit repository. Set the tag names in Settings to change that. Scanning
          does not need any settings.
        </Alert>
      )}

      {(running || (refreshStatus?.progress?.phases?.length ?? 0) > 0) && refreshStatus?.progress?.phases && (
        <ScanSteps phases={refreshStatus.progress.phases} running={running} />
      )}

      {/* The persistent migration Steps card: re-attached on page load, expanded
          on a Migrate click, collapsed once nothing but builds is in flight. */}
      {migrationRun?.progress?.phases?.length ? (
        <MigrationSteps
          phases={migrationRun.progress.phases}
          running={migrationRunning}
          summary={migrationRun.summary}
          startDate={migrationRun.startDate}
          totalSelected={migrationRun.progress.total_selected ?? undefined}
          expanded={stepsExpanded}
          onToggle={setStepsExpanded}
        />
      ) : null}

      <Container header={<Header variant="h2">My functions</Header>}>
        {/* Combined KeyValuePairs layout: a standalone hero (Functions) beside
            three titled groups (Deprecation / Priority / Migration), so the
            axes read apart instead of as one flat row. Empty numeric facts
            show a hyphen per the key-value-pairs guidelines. */}
        <KeyValuePairs
          columns={4}
          items={[
            {
              type: 'pair',
              label: 'Functions',
              value: (
                <SpaceBetween size="xxxs">
                  {/* Headline = ACTIVE deprecation backlog only (Red/Yellow, not
                      resolved). Resolved/green functions are excluded here but
                      remain in the table and filterable. */}
                  {/* First load: a skeleton the size of the big number, not a
                      hyphen that reads as "zero functions" for a second. */}
                  {firstLoad ? (
                    <Skeleton variant="text-display-l" width="3ch" />
                  ) : (
                    <Box variant="awsui-value-large">{summary.active || '-'}</Box>
                  )}
                  <Box variant="small" color="text-body-secondary">active backlog · {inScopeDetail}</Box>
                  {notChecked > 0 && (
                    <Box variant="small" color="text-body-secondary">
                      {`${notChecked} account${notChecked === 1 ? '' : 's'} not checked (Basic/Developer tier)`}
                    </Box>
                  )}
                  {/* The resolved toggle lives under the headline count because it
                      controls what that count (and the table) includes. */}
                  <Box padding={{ top: 'xxs' }}>
                    <Toggle checked={includeResolved} onChange={({ detail }) => setIncludeResolved(detail.checked)} disabled={resolvedCount === 0}>
                      Include migrated{resolvedCount ? ` (${resolvedCount})` : ''}
                    </Toggle>
                  </Box>
                </SpaceBetween>
              ),
            },
            {
              type: 'group',
              title: 'Deprecation',
              items: [
                { label: 'Deprecated / EOL', value: kpi(<StatusIndicator type={summary.red ? 'error' : 'success'}>{summary.red}</StatusIndicator>) },
                { label: 'Approaching', value: kpi(<StatusIndicator type={summary.yellow ? 'warning' : 'success'}>{summary.yellow}</StatusIndicator>) },
                { label: 'By runtime', value: kpi(<Box variant="small">{summary.topRuntimes}</Box>, '18ch') },
                // The AI urgency split describes the same backlog, so it lives
                // here rather than in a one-line column of its own.
                {
                  label: <SpaceBetween direction="horizontal" size="xxs"><span>Urgency</span><Icon name="gen-ai" size="small" ariaLabel="Generated by AI" /></SpaceBetween>,
                  value: kpi(<Box variant="small">{`Crit ${summary.urgency.CRITICAL} · High ${summary.urgency.HIGH} · Med ${summary.urgency.MEDIUM} · Low ${summary.urgency.LOW}`}</Box>, '24ch'),
                },
              ],
            },
            // Migration in TWO side-by-side groups so the container stays four
            // facts high (seven stacked facts doubled its height). Left: where
            // the runs stand (the funnel in). Right: what came out. EVERY fact
            // is always present (a zero is a fact too): facts that appeared only
            // when non-zero popped in after the others once data landed.
            // Not started + In progress + Rejected + Failed + No changes +
            // Branch ready = active backlog.
            {
              type: 'group',
              title: 'Migration runs',
              items: [
                // Labels come from the shared MIGRATION_RUN_STATUS map so the
                // hero, the table and the How it works page cannot drift.
                { label: MIGRATION_RUN_STATUS.NOT_STARTED.label, value: kpi(<StatusIndicator type="not-started">{summary.notStarted}</StatusIndicator>) },
                { label: MIGRATION_RUN_STATUS.IN_PROGRESS.label, value: kpi(<StatusIndicator type={summary.runInFlight ? 'in-progress' : 'pending'}>{summary.runInFlight}</StatusIndicator>) },
                { label: MIGRATION_RUN_STATUS.REJECTED.label, value: kpi(<StatusIndicator type={summary.runRejected ? 'stopped' : 'pending'}>{summary.runRejected}</StatusIndicator>) },
                { label: MIGRATION_RUN_STATUS.FAILED.label, value: kpi(<StatusIndicator type={summary.runFailed ? 'error' : 'pending'}>{summary.runFailed}</StatusIndicator>) },
              ],
            },
            {
              type: 'group',
              title: 'Migration outcomes',
              items: [
                { label: MIGRATION_RUN_STATUS.NO_CHANGES.label, value: kpi(<StatusIndicator type={summary.runNoChanges ? 'info' : 'pending'}>{summary.runNoChanges}</StatusIndicator>) },
                { label: MIGRATION_RUN_STATUS.BRANCH_READY.label, value: kpi(<StatusIndicator type={summary.runBranchReady ? 'success' : 'pending'}>{summary.runBranchReady}</StatusIndicator>) },
                // Inferred from Trusted Advisor no longer reporting the function
                // (the resolved axis), not from a run of ours.
                { label: 'Migrated', value: kpi(<StatusIndicator type={summary.migrated ? 'success' : 'pending'}>{summary.migrated}</StatusIndicator>) },
              ],
            },
          ]}
        />
      </Container>

      <Table
        {...collectionProps}
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        trackBy="item_key"
        items={items}
        columnDisplay={preferences.contentDisplay}
        pagination={<Pagination {...paginationProps} />}
        preferences={
          <CollectionPreferences
            title="Preferences" confirmLabel="Confirm" cancelLabel="Cancel"
            preferences={preferences}
            onConfirm={({ detail }) => { setPreferences(detail); localStorage.setItem(PREFS_KEY, JSON.stringify(detail)); }}
            pageSizePreference={{ title: 'Page size', options: [25, 50, 100].map((n) => ({ value: n, label: `${n} functions` })) }}
            contentDisplayPreference={{
              title: 'Columns',
              options: [
                { id: 'function', label: 'Function', alwaysVisible: true },
                { id: 'accountId', label: 'Account ID' }, { id: 'accountName', label: 'Account name' },
                { id: 'region', label: 'Region' },
                { id: 'runtime', label: 'Runtime' }, { id: 'deprecation', label: 'Deprecation' },
                { id: 'urgency', label: 'Priority' }, { id: 'assessment', label: 'Complexity' },
                { id: 'migration', label: 'Migration' },
              ],
            }}
          />
        }
        loading={loading}
        loadingText="Loading your functions"
        // First load only: skeleton rows under the real column headers instead
        // of a centered spinner (Cloudscape's loading pattern for tables).
        // Reloads (scan end, the 30 s migration poll) keep the real rows on
        // screen; passing the prop then would append placeholder rows under them.
        skeleton={firstLoad ? { totalRows: 'auto', maxAutoRows: 12, minAutoRows: 5, renderCell: renderSkeletonCell } : undefined}
        renderAriaLive={({ firstIndex, lastIndex, totalItemsCount }) => `Displaying functions ${firstIndex} to ${lastIndex} of ${totalItemsCount}`}
        variant="full-page"
        stickyHeader
        header={
          <Header
            variant="h1"
            // No "(0)" next to skeleton rows: the count appears with the data.
            counter={firstLoad ? undefined : shown === sorted.length ? `(${shown})` : `(${shown} of ${sorted.length})`}
            actions={
              <SpaceBetween size="xs" direction="horizontal" alignItems="center">
                {/* Discover (primary) runs the org-wide discover + assess scan.
                    Migrate resolves each selected function's repository, runs AWS
                    Transform and pushes the upgrade branch. Neither carries an
                    icon, so the pair reads consistently. */}
                <Button variant="primary" loading={running} onClick={rescan}>Discover</Button>
                <Button loading={migrating} disabled={selected.length === 0} onClick={() => migrate(selected)}>
                  Migrate{selected.length ? ` (${selected.length})` : ''}
                </Button>
              </SpaceBetween>
            }
          >
            My functions
          </Header>
        }
        filter={
          <PropertyFilter
            {...propertyFilterProps}
            filteringOptions={filteringOptions}
            i18nStrings={FILTER_I18N}
            countText={`${shown} match${shown === 1 ? '' : 'es'}`}
            expandToViewport
          />
        }
        empty={
          <Box textAlign="center" padding="l" color="text-body-secondary">
            <SpaceBetween size="xs">
              <Box variant="strong">No deprecated-runtime functions found</Box>
              <Box variant="p">Discover deprecated runtimes across the org.</Box>
              <Button variant="primary" onClick={rescan}>Discover</Button>
            </SpaceBetween>
          </Box>
        }
        columnDefinitions={[
          {
            id: 'function', header: 'Function', sortingField: 'function_arn',
            cell: (f) => <Button variant="inline-link" onClick={() => openDrawer(f)}>{funcName(f.function_arn)}</Button>,
          },
          ...(multipleAccounts ? [{
            id: 'accountId', header: 'Account ID', sortingField: 'account_id',
            cell: (f: MigrationFunction) => f.account_id
              ? <Box>{f.account_id}</Box>
              : <Box color="text-body-secondary">—</Box>,
          } as TableProps.ColumnDefinition<MigrationFunction>, {
            id: 'accountName', header: 'Account name', sortingField: 'account_name',
            cell: (f: MigrationFunction) => f.account_name
              ? <Box>{f.account_name}</Box>
              : <Box color="text-body-secondary">—</Box>,
          } as TableProps.ColumnDefinition<MigrationFunction>] : []),
          { id: 'region', header: 'Region', cell: (f) => f.region || '—', sortingField: 'region' },
          { id: 'runtime', header: 'Runtime', cell: (f) => f.runtime || '—', sortingField: 'runtime' },
          {
            // STATE ONLY — no inline dates (dates moved to the drawer). Red =
            // Deprecated, Yellow = Approaching EOL, green/resolved = Resolved.
            id: 'deprecation', header: 'Deprecation',
            sortingComparator: (a, b) => (a.days_to_deprecation ?? Number.MAX_SAFE_INTEGER) - (b.days_to_deprecation ?? Number.MAX_SAFE_INTEGER),
            cell: (f) => {
              const st = deprecationState(f);
              if (!st) return <Box color="text-body-secondary">—</Box>;
              const indicator = <StatusIndicator type={st.type}>{st.label}</StatusIndicator>;
              // The green Migrated state is INFERRED (TA never reports green), so
              // it carries a transparency note naming when it dropped out of TA.
              if (st.resolved && f.resolved_at) {
                return (
                  <Popover
                    dismissButton={false}
                    position="top"
                    size="medium"
                    triggerType="text"
                    content={`Inferred — no longer reported by Trusted Advisor as of ${f.resolved_at}`}
                  >
                    {indicator}
                  </Popover>
                );
              }
              return indicator;
            },
          },
          {
            id: 'urgency', header: <>Priority <Icon name="gen-ai" size="small" ariaLabel="Generated by AI" /></>,
            sortingComparator: (a, b) => urgencyRank(a) - urgencyRank(b),
            cell: (f) => f.priority_label ? <StatusIndicator type={PRIORITY_INDICATOR[f.priority_label] || 'pending'}>{titleCase(f.priority_label)}</StatusIndicator> : <Box color="text-body-secondary">—</Box>,
          },
          {
            id: 'assessment', header: <>Complexity <Icon name="gen-ai" size="small" ariaLabel="Generated by AI" /></>,
            cell: (f) => f.migration_complexity
              ? <StatusIndicator type="success">{titleCase(f.migration_complexity)}</StatusIndicator>
              : <StatusIndicator type="not-started">Not assessed</StatusIndicator>,
          },
          {
            // Single Migration axis: the live mig# run state when there is one,
            // else the inferred-MIGRATED state, else "Not started". Branch ready
            // links to the pushed upgrade branch in the CodeCommit console.
            id: 'migration', header: 'Migration',
            cell: (f) => renderMigrationRun(f),
          },
        ]}
      />
    </SpaceBetween>
  );
}
