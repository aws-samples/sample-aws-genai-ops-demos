import { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Box from '@cloudscape-design/components/box';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Button from '@cloudscape-design/components/button';
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar';
import Popover from '@cloudscape-design/components/popover';
import Table from '@cloudscape-design/components/table';
import Tabs from '@cloudscape-design/components/tabs';
import Badge from '@cloudscape-design/components/badge';
import Link from '@cloudscape-design/components/link';
import {
  getLifecycleData, getScanners, startRefreshAll, getRefreshStatus,
  DeprecationItem, RefreshProgress, ScanCoverage,
} from '../api';
import {
  statusMeta, isConcern, getDeadline, urgencyOf, formatDaysLeft, formatDate,
  urgencySort, serviceLabel, itemName, resourceCount, resourceWord, costExposure, formatUsd,
} from '../lifecycle';

// sessionStorage key for the in-flight refresh execution ARN. The pipeline
// runs server-side as a Lambda durable execution; this only lets the UI
// re-attach to it after a page navigation or reload.
const REFRESH_ARN_KEY = 'lifecycle-refresh-execution-arn';

const total = (rows: DeprecationItem[]) =>
  rows.reduce((n, r) => n + (Number(r.service_specific?.total_affected) || 0), 0);

const relative = (iso: string | null | undefined): string => {
  if (!iso) return 'never';
  const ms = Date.now() - new Date(iso).getTime();
  const h = Math.round(ms / 3_600_000);
  if (h < 1) return 'less than an hour ago';
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
};

export default function Dashboard() {
  const navigate = useNavigate();
  const [inventory, setInventory] = useState<DeprecationItem[]>([]);
  const [facts, setFacts] = useState<DeprecationItem[]>([]);
  const [coverage, setCoverage] = useState<ScanCoverage | null>(null);
  const [loading, setLoading] = useState(true);
  const [flashbarItems, setFlashbarItems] = useState<FlashbarProps.MessageDefinition[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [progress, setProgress] = useState<RefreshProgress | null>(null);
  const pollingRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    loadData();
    // Re-attach to an in-flight refresh (e.g. after navigating away and back).
    const inFlightArn = sessionStorage.getItem(REFRESH_ARN_KEY);
    if (inFlightArn) {
      setRefreshing(true);
      pollExecution(inFlightArn);
    }
    return () => {
      if (pollingRef.current) clearTimeout(pollingRef.current);
    };
  }, []);

  const flash = (type: FlashbarProps.Type, content: string) =>
    setFlashbarItems([{
      type, content, dismissible: true, dismissLabel: 'Dismiss',
      onDismiss: () => setFlashbarItems([]), id: `${type}-${Date.now()}`,
    }]);

  const loadData = async (showLoading = true) => {
    try {
      if (showLoading) setLoading(true);
      const [data, cov] = await Promise.all([getLifecycleData(), getScanners()]);
      setInventory(data.inventory);
      setFacts(data.facts);
      setCoverage(cov);
    } catch (err: any) {
      flash('error', `Failed to load data: ${err.message}`);
    } finally {
      if (showLoading) setLoading(false);
    }
  };

  // Observe a server-side refresh until it reaches a terminal state. Polling
  // is passive: stopping it (unmount) never affects the run.
  const pollExecution = async (executionArn: string, consecutiveErrors = 0) => {
    try {
      const execution = await getRefreshStatus(executionArn);
      if (execution.status === 'RUNNING') {
        setProgress(execution.progress || null);
        loadData(false);
        pollingRef.current = setTimeout(() => pollExecution(executionArn), 5000);
        return;
      }
      sessionStorage.removeItem(REFRESH_ARN_KEY);
      setRefreshing(false);
      setProgress(null);
      await loadData(false);

      if (execution.status === 'SUCCEEDED') {
        const ex = execution.summary?.extract;
        const sc = execution.summary?.scan;
        const failures = [...(ex?.failed || []), ...(sc?.failed_cells || [])];
        const detail = ex && sc
          ? `${sc.items_discovered} resource groups found in your account, ${sc.needs_attention} need attention. ` +
            `Catalog: ${ex.succeeded}/${ex.total} services, ${ex.items_extracted} facts.`
          : '';
        flash(failures.length ? 'warning' : 'success',
          failures.length ? `Refresh finished with failures. ${detail} Failed: ${failures.join(', ')}`
                          : `Refresh complete. ${detail}`);
      } else {
        flash('error', `Refresh ended with status ${execution.status}. Check the pipeline function's durable executions in the Lambda console.`);
      }
    } catch (error: any) {
      if (consecutiveErrors < 3) {
        pollingRef.current = setTimeout(() => pollExecution(executionArn, consecutiveErrors + 1), 5000);
      } else {
        setRefreshing(false);
        flash('warning', 'Lost track of the refresh progress, but it keeps running server-side. Reload the page to re-attach.');
      }
    }
  };

  const handleRefresh = async () => {
    try {
      setRefreshing(true);
      const { executionArn, alreadyRunning } = await startRefreshAll();
      sessionStorage.setItem(REFRESH_ARN_KEY, executionArn);
      flash('info', alreadyRunning
        ? 'A refresh is already in progress - showing its status.'
        : 'Refresh started: updating the catalog from the AWS documentation, then scanning your account. It runs server-side - you can navigate away.');
      pollExecution(executionArn);
    } catch (err: any) {
      setRefreshing(false);
      flash('error', `Failed to start refresh: ${err.message}`);
    }
  };

  // ---- derived numbers (all from the inventory, i.e. MY resources) ----------
  const exposure = useMemo(() => {
    const concerns = inventory.filter((r) => isConcern(r.status));
    const byUrgency = { past: [] as DeprecationItem[], soon: [] as DeprecationItem[], year: [] as DeprecationItem[], later: [] as DeprecationItem[] };
    for (const r of concerns) {
      const u = urgencyOf(getDeadline(r));
      if (r.status === 'end_of_life' || u === 'past') byUrgency.past.push(r);
      else if (u === 'soon') byUrgency.soon.push(r);
      else if (u === 'year' || u === 'none') byUrgency.year.push(r);
      else byUrgency.later.push(r);
    }
    const fine = inventory.filter((r) => !isConcern(r.status));
    return { concerns, byUrgency, fine, all: inventory };
  }, [inventory]);

  const deadlines = useMemo(() => [...exposure.concerns].sort(urgencySort).slice(0, 10), [exposure]);

  const byService = useMemo(() => {
    const m = new Map<string, { rows: number; resources: number; concerns: number; worst: number }>();
    for (const r of inventory) {
      const e = m.get(r.service_name) || { rows: 0, resources: 0, concerns: 0, worst: 99 };
      e.rows += 1;
      e.resources += Number(r.service_specific?.total_affected) || 0;
      if (isConcern(r.status)) e.concerns += 1;
      e.worst = Math.min(e.worst, statusMeta(r.status).rank);
      m.set(r.service_name, e);
    }
    return [...m.entries()].sort((a, b) => a[1].worst - b[1].worst || b[1].concerns - a[1].concerns);
  }, [inventory]);

  const lastFactsRefresh = useMemo(
    () => facts.reduce<string | null>((max, f) => (!max || f.last_verified > max ? f.last_verified : max), null),
    [facts]);
  const factServices = useMemo(() => new Set(facts.map((f) => f.service_name)).size, [facts]);

  if (loading) {
    return (
      <Container>
        <Box textAlign="center" padding="xxl">
          <StatusIndicator type="loading">Loading your exposure...</StatusIndicator>
        </Box>
      </Container>
    );
  }

  const kpi = (label: string, value: number | string, sub: string, color?: 'text-status-error' | 'text-status-warning' | 'text-status-success' | 'text-status-info', onClick?: () => void) => (
    <div>
      <Box variant="awsui-key-label">{label}</Box>
      <Box variant="h1" fontSize="display-l" fontWeight="bold" color={color}>
        {onClick ? <Link onFollow={(e) => { e.preventDefault(); onClick(); }} fontSize="display-l" href="#">{value}</Link> : value}
      </Box>
      <Box variant="small" color="text-body-secondary">{sub}</Box>
    </div>
  );

  // RDS/Aurora Extended Support surcharge across the inventory (#142)
  const money = useMemo(() => {
    let monthly = 0, forecast = 0, priced = 0, now = 0;
    for (const r of inventory) {
      const c = costExposure(r);
      if (!c) continue;
      monthly += c.monthly; forecast += c.forecast_12m; priced += c.resources_priced; now += c.in_extended_support;
    }
    return { monthly, forecast, priced, now };
  }, [inventory]);

  const goResources = (status?: string) => navigate(status ? `/resources?status=${status}` : '/resources');

  return (
    <SpaceBetween size="l">
      <Flashbar items={flashbarItems} stackItems />

      <Container
        header={
          <Header
            variant="h1"
            description="Resources in this account running versions that AWS is retiring, matched against the deprecation facts published in the AWS documentation."
            actions={
              <SpaceBetween direction="horizontal" size="xxs">
                <Button variant="primary" iconName="refresh" loading={refreshing} onClick={handleRefresh} disabled={refreshing}>
                  {refreshing
                    ? progress ? `Refreshing... (${progress.extract_done} extracted, ${progress.scan_done} scanned)` : 'Refreshing...'
                    : 'Refresh'}
                </Button>
                <Popover dismissButton={false} position="bottom" size="medium" triggerType="text"
                  content={
                    <SpaceBetween size="xs">
                      <Box variant="strong">One end-to-end run (Lambda durable function):</Box>
                      <Box variant="small">1. Updates the catalog: deprecation facts from the AWS documentation for every enabled service.</Box>
                      <Box variant="small">
                        2. Scans this account with {coverage?.scanners.length ?? 0} scanners
                        {coverage ? ` (${coverage.scanners.map((s) => s.label).join(', ')})` : ''} and matches what it finds against the catalog.
                      </Box>
                      <Box variant="small">3. Reconciles your inventory and publishes a summary to SNS.</Box>
                      <Box variant="small" color="text-body-secondary">The run continues server-side if you navigate away.</Box>
                    </SpaceBetween>
                  }>
                  <Box color="text-status-info" display="inline">ⓘ</Box>
                </Popover>
              </SpaceBetween>
            }
          >
            My exposure
          </Header>
        }
      >
        <SpaceBetween size="l">
          <ColumnLayout columns={money.priced ? 5 : 4} variant="text-grid">
            {kpi('Past end of life', total(exposure.byUrgency.past), `${exposure.byUrgency.past.length} version${exposure.byUrgency.past.length === 1 ? '' : 's'} - act now`, 'text-status-error', () => goResources('end_of_life'))}
            {kpi('Ending within 90 days', total(exposure.byUrgency.soon), `${exposure.byUrgency.soon.length} version${exposure.byUrgency.soon.length === 1 ? '' : 's'} - plan the upgrade`, 'text-status-error', () => goResources())}
            {kpi('Ending within a year', total(exposure.byUrgency.year), `${exposure.byUrgency.year.length} version${exposure.byUrgency.year.length === 1 ? '' : 's'} - schedule it`, 'text-status-warning', () => goResources())}
            {kpi('Fine for now', total(exposure.fine), `${exposure.fine.length} version${exposure.fine.length === 1 ? '' : 's'} supported or not matched`, 'text-status-success', () => goResources('supported'))}
            {money.priced > 0 && kpi('Extended Support, next 12 months', formatUsd(money.forecast),
              money.now ? `${formatUsd(money.monthly)}/month once all in Extended Support · ${money.now} billing now` : `${formatUsd(money.monthly)}/month once all ${money.priced} RDS/Aurora resources are in Extended Support`,
              money.now ? 'text-status-error' : 'text-status-warning', () => navigate('/resources?service=rds&status=all'))}
          </ColumnLayout>

          <Box variant="small" color="text-body-secondary">
            <StatusIndicator type={coverage?.last_scan.last_verified ? 'success' : 'pending'}>
              Account scan: {coverage?.last_scan.resources ?? 0} resource groups across {byService.length} services
              {coverage?.last_scan.regions.length ? ` in ${coverage.last_scan.regions.join(', ')}` : ''}, {relative(coverage?.last_scan.last_verified)}
            </StatusIndicator>
            {'   '}
            <StatusIndicator type={facts.length ? 'success' : 'pending'}>
              Catalog: {facts.length} facts across {factServices} services, refreshed {relative(lastFactsRefresh)}
            </StatusIndicator>
          </Box>
        </SpaceBetween>
      </Container>

      <Tabs
        tabs={[
          {
            id: 'deadlines',
            label: `Next deadlines (${exposure.concerns.length})`,
            content: (
              <Table
                variant="embedded"
                items={deadlines}
                trackBy="item_id"
                empty={
                  <Box textAlign="center" padding="l" color="text-body-secondary">
                    <Box variant="strong">Nothing in your account is on a retiring version</Box>
                    <Box variant="p">Last scan {relative(coverage?.last_scan.last_verified)}. Click Refresh to scan again.</Box>
                  </Box>
                }
                columnDefinitions={[
                  { id: 'service', header: 'Service', cell: (r) => <Badge color="blue">{serviceLabel(r.service_name)}</Badge> },
                  { id: 'name', header: 'Version', cell: (r) => (
                    <SpaceBetween size="xxxs">
                      <Link onFollow={(e) => { e.preventDefault(); navigate(`/resources?q=${encodeURIComponent(r.service_specific?.identifier || '')}`); }} href="#">
                        {itemName(r)}
                      </Link>
                      <Box variant="small" color="text-body-secondary">{r.service_specific?.identifier}</Box>
                    </SpaceBetween>
                  ) },
                  { id: 'status', header: 'Status', cell: (r) => <StatusIndicator type={statusMeta(r.status).indicator}>{statusMeta(r.status).label}</StatusIndicator> },
                  { id: 'deadline', header: 'Deadline', cell: (r) => {
                    const d = getDeadline(r);
                    return d ? <SpaceBetween size="xxxs"><Box>{formatDate(d.date)}</Box><Box variant="small" color="text-body-secondary">{formatDaysLeft(d)}</Box></SpaceBetween> : <Box color="text-body-secondary">-</Box>;
                  } },
                  { id: 'resources', header: 'My resources', cell: (r) => {
                    const n = resourceCount(r);
                    return n
                      ? <Link onFollow={(e) => { e.preventDefault(); navigate(`/resources?status=all&details=${encodeURIComponent(r.item_id)}`); }} href="#">{n} {resourceWord(n)}</Link>
                      : <Box color="text-body-secondary">0</Box>;
                  } },
                  { id: 'region', header: 'Region', cell: (r) => r.region || '-' },
                ]}
                footer={exposure.concerns.length > deadlines.length && (
                  <Box textAlign="center"><Link onFollow={(e) => { e.preventDefault(); goResources(); }} href="#">See all {exposure.concerns.length} in My resources</Link></Box>
                )}
              />
            ),
          },
          {
            id: 'services',
            label: 'By service',
            content: (
              <Table
                variant="embedded"
                items={byService}
                trackBy={([k]) => k}
                empty={<Box textAlign="center" padding="l" color="text-body-secondary">No resources scanned yet</Box>}
                columnDefinitions={[
                  { id: 'service', header: 'Service', cell: ([k]) => <Link onFollow={(e) => { e.preventDefault(); navigate(`/resources?service=${k}`); }} href="#">{serviceLabel(k)}</Link> },
                  { id: 'resources', header: 'Resources', cell: ([, v]) => v.resources },
                  { id: 'versions', header: 'Versions in use', cell: ([, v]) => v.rows },
                  { id: 'concerns', header: 'Need attention', cell: ([, v]) => v.concerns
                      ? <StatusIndicator type={v.worst <= 1 ? 'error' : 'warning'}>{v.concerns} of {v.rows}</StatusIndicator>
                      : <StatusIndicator type="success">none</StatusIndicator> },
                ]}
              />
            ),
          },
        ]}
      />
    </SpaceBetween>
  );
}
