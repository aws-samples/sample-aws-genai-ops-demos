// Statistics — the measurement view of the migration demo.
//
// Six charts answer "are we making progress, and where is the risk?":
//   1. Fleet remediation over time   (hero, stacked area, from the rollup series)
//   2. Migration pipeline now        (horizontal bar, the six run statuses)
//   3. Fleet status now              (pie) + KPI tiles (backlog / migrated / branches / %)
//   4. Remaining backlog by runtime  (horizontal bar)
//   5. Account risk map              (bubble — Highcharts)
//   6. Risk vs usage                 (scatter — Highcharts)
//
// Charts 1-4 use the legacy Cloudscape chart components (area/pie/bar). Cloudscape
// v3 (3.0.1401, the installed version) ships NO cartesian-chart component, so the
// bubble and scatter charts, which Cloudscape cannot draw, use Highcharts. The
// highcharts-more (bubble) and accessibility modules are registered once in the
// shared components/charts/highcharts-setup module the charts import from.
// Modeled on ScanLogPage: ContentLayout + Header, load on mount, Cloudscape
// loading / error (+Retry) / empty conventions.
import { useState, useEffect, useCallback, useMemo } from 'react';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Grid from '@cloudscape-design/components/grid';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import AreaChart from '@cloudscape-design/components/area-chart';
import BarChart from '@cloudscape-design/components/bar-chart';
import PieChart from '@cloudscape-design/components/pie-chart';
import Skeleton from '@cloudscape-design/components/skeleton';
import LiveRegion from '@cloudscape-design/components/live-region';
import Link from '@cloudscape-design/components/link';
import { useNavigate } from 'react-router-dom';
import { getFunctions, getStats, MigrationFunction, RollupRecord } from '../api';
import { MIGRATION_RUN_STATUS } from '../constants';
import { deprecationState, isActiveBacklog, tallyMigrationRuns } from '../migration-tallies';
import AccountRiskBubbleChart from '../components/charts/AccountRiskBubbleChart';
import RiskVsUsageScatterChart from '../components/charts/RiskVsUsageScatterChart';

// Cloudscape chart status strings (kept identical across all charts on the page).
const CHART_I18N = {
  loadingText: 'Loading chart',
  errorText: "The data couldn't be fetched. Try again later.",
  recoveryText: 'Retry',
  empty: 'There is no data available.',
};

// Status data-viz colors: red = error, yellow = warning, green = success,
// blue = info / in-progress, grey = stopped (dark) / not started (light). The
// two blues and two greys are different shades on purpose so adjacent bars of
// the pipeline chart stay distinguishable.
const COLOR_RED = '#d91515';
const COLOR_YELLOW = '#e07941';
const COLOR_GREEN = '#037f0c';
const COLOR_BLUE = '#0972d3';
const COLOR_BLUE_LIGHT = '#539fe5';
const COLOR_GREY = '#5f6b7a';
const COLOR_GREY_LIGHT = '#8d99a8';

// Header copy shared by the loaded page and its skeleton (Cloudscape: headers
// keep their real text while loading), so the two can never drift.
const HERO_DESCRIPTION = 'Active backlog, upgrade branches ready for merge, and functions migrated to date, over successive scans.';
const PIPELINE_DESCRIPTION = 'Where every active function stands in the migration flow. Branch ready means an upgrade branch was pushed to the repository; the pull request, review and merge happen there.';
const BRANCHES_READY_LABEL = 'Branches ready (awaiting merge)';

const isRed = (f: MigrationFunction) => deprecationState(f)?.label === 'Deprecated';
const isYellow = (f: MigrationFunction) => deprecationState(f)?.label === 'Approaching EOL';

// --- Chart 1: Fleet remediation over time (hero) ---------------------------

function RemediationOverTimeChart({ rollups }: { rollups: RollupRecord[] }) {
  // Sort ascending by rollup_at so every series x is ascending (Highcharts/
  // Cloudscape both expect ordered x). The backend returns them oldest-first, but
  // sorting here keeps the chart honest regardless of input order.
  // The area chart is stacked, so a branch-ready function is drawn ONCE: the
  // red series is the backlog with no branch yet, blue "Branches ready" sits on
  // top (red + blue = the full active backlog), green "Migrated" is unchanged.
  // Rollups written before the run axis have no run_branches_ready -> 0, and
  // the hero draws exactly as it did before.
  const series = useMemo(() => {
    const sorted = [...rollups].sort((a, b) => new Date(a.rollup_at).getTime() - new Date(b.rollup_at).getTime());
    const backlog = sorted.map((r) => {
      const branches = r.run_branches_ready || 0;
      return { x: new Date(r.rollup_at), y: Math.max(0, (r.red || 0) + (r.yellow || 0) - branches) };
    });
    const branches = sorted.map((r) => ({ x: new Date(r.rollup_at), y: r.run_branches_ready || 0 }));
    const migrated = sorted.map((r) => ({ x: new Date(r.rollup_at), y: r.migrated_total || 0 }));
    return { backlog, branches, migrated };
  }, [rollups]);

  if (rollups.length < 2) {
    return (
      <Box color="text-body-secondary" textAlign="center" padding="l">
        Not enough history yet — run at least two scans to chart remediation over time.
      </Box>
    );
  }

  return (
    <AreaChart
      height={300}
      series={[
        { title: 'Active backlog', type: 'area', data: series.backlog, color: COLOR_RED },
        { title: 'Branches ready', type: 'area', data: series.branches, color: COLOR_BLUE },
        { title: 'Migrated', type: 'area', data: series.migrated, color: COLOR_GREEN },
      ]}
      xScaleType="time"
      xTitle="Scan time"
      yTitle="Functions"
      ariaLabel="Area chart showing the fleet backlog, branches ready and migrated functions over time"
      ariaDescription="Stacked area chart showing the active deprecated backlog, the upgrade branches ready for merge, and the functions migrated to date over successive scans."
      hideFilter
      statusType="finished"
      empty={CHART_I18N.empty}
      xTickFormatter={(d) => (d instanceof Date ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : String(d))}
    />
  );
}

// --- Chart 2: Migration pipeline now (horizontal bar) ----------------------

// One bar per run status, in flow order. Cloudscape colours per SERIES, so each
// status is its own single-point series on a fixed categorical domain: stacked,
// with one non-empty series per category, that renders one plain bar per row
// and the legend doubles as the status key. Labels come from
// MIGRATION_RUN_STATUS only; colours follow each status indicator's semantics.
// Scope is the ACTIVE backlog: Not started + In progress + Rejected + Failed +
// No changes + Branch ready + unknown = active functions. An unknown status
// never gets a bar; it is called out in a note below the chart instead.
function MigrationPipelineChart({ functions }: { functions: MigrationFunction[] }) {
  const tallies = useMemo(() => tallyMigrationRuns(functions.filter(isActiveBacklog)), [functions]);

  const steps: Array<[string, number, string]> = [
    [MIGRATION_RUN_STATUS.NOT_STARTED.label, tallies.notStarted, COLOR_GREY_LIGHT],
    [MIGRATION_RUN_STATUS.IN_PROGRESS.label, tallies.runInFlight, COLOR_BLUE],
    [MIGRATION_RUN_STATUS.REJECTED.label, tallies.runRejected, COLOR_GREY],
    [MIGRATION_RUN_STATUS.FAILED.label, tallies.runFailed, COLOR_RED],
    [MIGRATION_RUN_STATUS.NO_CHANGES.label, tallies.runNoChanges, COLOR_BLUE_LIGHT],
    [MIGRATION_RUN_STATUS.BRANCH_READY.label, tallies.runBranchReady, COLOR_GREEN],
  ];
  const domain = steps.map(([label]) => label);

  if (tallies.active === 0) {
    return <Box color="text-body-secondary" textAlign="center" padding="l">{CHART_I18N.empty}</Box>;
  }

  const legacy = tallies.runLegacy;
  return (
    <SpaceBetween size="s">
      <BarChart
        height={300}
        series={steps.map(([label, n, color]) => ({
          title: label,
          type: 'bar' as const,
          // Every series spans the whole domain (0 elsewhere) so Cloudscape never
          // sees a category one series lacks; visually one bar per status.
          data: domain.map((x) => ({ x, y: x === label ? n : 0 })),
          color,
        }))}
        xDomain={domain}
        xScaleType="categorical"
        stackedBars
        horizontalBars
        xTitle="Migration status"
        yTitle="Active functions"
        ariaLabel="Bar chart showing where every active function stands in the migration flow"
        ariaDescription="Horizontal bar chart with one bar per migration status, in flow order: not started, in progress, rejected, failed, no changes, and branch ready. Counts are active functions."
        hideFilter
        statusType="finished"
        empty={CHART_I18N.empty}
      />
      {legacy > 0 && (
        <Box variant="small" color="text-body-secondary">
          {`${legacy} function${legacy === 1 ? '' : 's'} carry a migration status from an earlier version of this demo`}
        </Box>
      )}
    </SpaceBetween>
  );
}

// --- Chart 3: Fleet status now (pie) + KPI tiles ---------------------------

function FleetStatusNow({ functions }: { functions: MigrationFunction[] }) {
  // Deprecation facts and Branch ready come from the shared tally helper (the
  // same one My functions reads). `migrated` lives on resolved rows, so it is
  // tallied over the whole list; the rest over the active backlog.
  const facts = useMemo(() => {
    const activeRows = functions.filter(isActiveBacklog);
    const all = tallyMigrationRuns(functions);
    const a = tallyMigrationRuns(activeRows);
    // The pie draws a branch-ready function ONCE, so split the Branch ready
    // slice out of Deprecated / Approaching EOL by colour.
    let branchRed = 0;
    let branchYellow = 0;
    for (const f of activeRows) {
      if (f.migration_run_status !== 'BRANCH_READY') continue;
      if (isRed(f)) branchRed += 1; else if (isYellow(f)) branchYellow += 1;
    }
    const denom = a.active + all.migrated;
    const pctRemediated = denom > 0 ? Math.round((all.migrated / denom) * 100) : 0;
    return {
      deprecated: a.red, approaching: a.yellow, branchesReady: a.runBranchReady,
      active: a.active, migrated: all.migrated, pctRemediated, branchRed, branchYellow,
    };
  }, [functions]);

  // Slices sum to active + migrated.
  const pieData = useMemo(
    () => [
      { title: 'Deprecated', value: facts.deprecated - facts.branchRed, color: COLOR_RED },
      { title: 'Approaching EOL', value: facts.approaching - facts.branchYellow, color: COLOR_YELLOW },
      { title: MIGRATION_RUN_STATUS.BRANCH_READY.label, value: facts.branchesReady, color: COLOR_BLUE },
      { title: 'Migrated', value: facts.migrated, color: COLOR_GREEN },
    ].filter((d) => d.value > 0),
    [facts],
  );

  return (
    <SpaceBetween size="l">
      <KeyValuePairs
        columns={4}
        items={[
          {
            label: 'Remaining backlog',
            value: <Box variant="awsui-value-large">{facts.active || '-'}</Box>,
          },
          {
            label: 'Migrated to date',
            value: <Box variant="awsui-value-large">{facts.migrated || '-'}</Box>,
          },
          {
            label: BRANCHES_READY_LABEL,
            value: <Box variant="awsui-value-large">{facts.branchesReady || '-'}</Box>,
          },
          {
            label: '% remediated',
            value: (
              <StatusIndicator type={facts.pctRemediated >= 100 ? 'success' : 'in-progress'}>
                {`${facts.pctRemediated}%`}
              </StatusIndicator>
            ),
          },
        ]}
      />
      {pieData.length === 0 ? (
        <Box color="text-body-secondary" textAlign="center" padding="l">{CHART_I18N.empty}</Box>
      ) : (
        <PieChart
          size="medium"
          data={pieData}
          ariaLabel="Pie chart showing the fleet status now"
          ariaDescription="Pie chart showing deprecated, approaching end of life, branch ready (upgrade branch pushed, awaiting merge), and migrated functions."
          hideFilter
          statusType="finished"
          detailPopoverContent={(d) => [{ key: 'Functions', value: String(d.value) }]}
          segmentDescription={(d, sum) => `${d.value} functions, ${sum ? Math.round((d.value / sum) * 100) : 0}%`}
          empty={CHART_I18N.empty}
        />
      )}
    </SpaceBetween>
  );
}

// --- Chart 4: Remaining backlog by runtime (horizontal bar) ----------------

function BacklogByRuntimeChart({ functions }: { functions: MigrationFunction[] }) {
  const { runtimes, redData, yellowData } = useMemo(() => {
    const red = new Map<string, number>();
    const yellow = new Map<string, number>();
    const totals = new Map<string, number>();
    for (const f of functions) {
      if (!isActiveBacklog(f)) continue;
      const rt = f.runtime || 'unknown';
      totals.set(rt, (totals.get(rt) || 0) + 1);
      if (isRed(f)) red.set(rt, (red.get(rt) || 0) + 1);
      else if (isYellow(f)) yellow.set(rt, (yellow.get(rt) || 0) + 1);
    }
    // Top ~7 runtimes by total active count.
    const top = [...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 7).map(([rt]) => rt);
    return {
      runtimes: top,
      redData: top.map((rt) => ({ x: rt, y: red.get(rt) || 0 })),
      yellowData: top.map((rt) => ({ x: rt, y: yellow.get(rt) || 0 })),
    };
  }, [functions]);

  if (runtimes.length === 0) {
    return <Box color="text-body-secondary" textAlign="center" padding="l">{CHART_I18N.empty}</Box>;
  }

  return (
    <BarChart
      height={300}
      series={[
        { title: 'Deprecated', type: 'bar', data: redData, color: COLOR_RED },
        { title: 'Approaching EOL', type: 'bar', data: yellowData, color: COLOR_YELLOW },
      ]}
      xScaleType="categorical"
      stackedBars
      horizontalBars
      xTitle="Runtime"
      yTitle="Active functions"
      ariaLabel="Bar chart showing the remaining backlog by runtime"
      ariaDescription="Horizontal stacked bar chart showing active deprecated and approaching end-of-life functions per runtime."
      hideFilter
      statusType="finished"
      empty={CHART_I18N.empty}
    />
  );
}

// --- Page ------------------------------------------------------------------

// Loading state as a skeleton of the SAME layout as the loaded page (hero
// chart, full-width pipeline chart, then two 2-up rows), so nothing jumps when
// the data lands. Headers keep their real text (Cloudscape: never skeleton a
// heading; the skeleton stays non-interactive, so no Link); only the chart
// areas and the four big numbers are placeholders. Skeletons are aria-hidden,
// so a hidden live region carries the announcement.
const CHART_HEIGHT = '300px';
const twoUp = [{ colspan: { default: 12, m: 6 } }, { colspan: { default: 12, m: 6 } }];

function StatisticsSkeleton() {
  return (
    <LiveRegion>
      <LiveRegion hidden>Loading statistics</LiveRegion>
      <SpaceBetween size="l">
        <Container header={<Header variant="h2" description={HERO_DESCRIPTION}>Fleet remediation over time</Header>}>
          <Skeleton height={CHART_HEIGHT} />
        </Container>
        <Container header={<Header variant="h2" description={PIPELINE_DESCRIPTION}>Migration pipeline now</Header>}>
          <Skeleton height={CHART_HEIGHT} />
        </Container>
        <Grid gridDefinition={twoUp}>
          <Container header={<Header variant="h2" description="Where the fleet stands right now.">Fleet status now</Header>}>
            <SpaceBetween size="l">
              <KeyValuePairs
                columns={4}
                items={[
                  { label: 'Remaining backlog', value: <Skeleton variant="text-display-l" width="3ch" /> },
                  { label: 'Migrated to date', value: <Skeleton variant="text-display-l" width="3ch" /> },
                  { label: BRANCHES_READY_LABEL, value: <Skeleton variant="text-display-l" width="3ch" /> },
                  { label: '% remediated', value: <Skeleton width="5ch" /> },
                ]}
              />
              <Skeleton height="200px" />
            </SpaceBetween>
          </Container>
          <Container header={<Header variant="h2" description="Active deprecated functions by runtime, deprecated versus approaching.">Remaining backlog by runtime</Header>}>
            <Skeleton height={CHART_HEIGHT} />
          </Container>
        </Grid>
        <Grid gridDefinition={twoUp}>
          <Container header={<Header variant="h2" description="Each account by backlog size and urgency; bubble size is critical-priority functions.">Account risk map</Header>}>
            <Skeleton height={CHART_HEIGHT} />
          </Container>
          <Container header={<Header variant="h2" description="Each function by time to end of life versus how busy it is.">Risk vs usage</Header>}>
            <Skeleton height={CHART_HEIGHT} />
          </Container>
        </Grid>
      </SpaceBetween>
    </LiveRegion>
  );
}

export default function StatisticsPage() {
  const [functions, setFunctions] = useState<MigrationFunction[]>([]);
  const [rollups, setRollups] = useState<RollupRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [fns, stats] = await Promise.all([getFunctions(), getStats()]);
      setFunctions(fns.functions);
      setRollups(stats.rollups);
    } catch (err) {
      setError((err instanceof Error && err.message) || "The data couldn't be fetched. Try again later.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const hasData = functions.length > 0 || rollups.length > 0;

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Progress and risk across the fleet — remediation over time, status now, and where the risk concentrates."
        >
          Statistics
        </Header>
      }
    >
      {loading ? (
        <StatisticsSkeleton />
      ) : error ? (
        <Container>
          <SpaceBetween size="s">
            <StatusIndicator type="error">{CHART_I18N.errorText}</StatusIndicator>
            <Button iconName="refresh" onClick={load}>Retry</Button>
          </SpaceBetween>
        </Container>
      ) : !hasData ? (
        <Container>
          <Box color="text-body-secondary" textAlign="center" padding="l">{CHART_I18N.empty}</Box>
        </Container>
      ) : (
        <SpaceBetween size="l">
          {/* Hero — full width */}
          <Container header={<Header variant="h2" description={HERO_DESCRIPTION}>Fleet remediation over time</Header>}>
            <RemediationOverTimeChart rollups={rollups} />
          </Container>

          {/* Migration pipeline — full width */}
          <Container
            header={
              <Header
                variant="h2"
                description={
                  <>
                    {PIPELINE_DESCRIPTION}{' '}
                    <Link href="/how-it-works" onFollow={(e) => { e.preventDefault(); navigate('/how-it-works'); }}>How it works</Link>
                  </>
                }
              >
                Migration pipeline now
              </Header>
            }
          >
            <MigrationPipelineChart functions={functions} />
          </Container>

          {/* The rest, 2-up */}
          <Grid gridDefinition={[{ colspan: { default: 12, m: 6 } }, { colspan: { default: 12, m: 6 } }]}>
            <Container header={<Header variant="h2" description="Where the fleet stands right now.">Fleet status now</Header>}>
              <FleetStatusNow functions={functions} />
            </Container>
            <Container header={<Header variant="h2" description="Active deprecated functions by runtime, deprecated versus approaching.">Remaining backlog by runtime</Header>}>
              <BacklogByRuntimeChart functions={functions} />
            </Container>
          </Grid>

          <Grid gridDefinition={[{ colspan: { default: 12, m: 6 } }, { colspan: { default: 12, m: 6 } }]}>
            <Container header={<Header variant="h2" description="Each account by backlog size and urgency; bubble size is critical-priority functions.">Account risk map</Header>}>
              <AccountRiskBubbleChart functions={functions} />
            </Container>
            <Container header={<Header variant="h2" description="Each function by time to end of life versus how busy it is.">Risk vs usage</Header>}>
              <RiskVsUsageScatterChart functions={functions} />
            </Container>
          </Grid>
        </SpaceBetween>
      )}
    </ContentLayout>
  );
}
