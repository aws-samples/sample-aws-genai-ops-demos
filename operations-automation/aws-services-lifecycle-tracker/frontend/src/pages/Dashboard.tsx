import { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
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
  statusMeta, isConcern, getDeadline, formatDaysLeft, formatDate, exposureBucket, EXPOSURE_BUCKETS, distinctVersions,
  urgencySort, serviceLabel, itemName, resourceCount, resourceWord, costExposure, formatUsd, costTimeline, formatMonth,
} from '../lifecycle';
import { SupportTierCell, SupportTierHeader } from '../components/SupportTierCell';
import RefreshSteps from '../components/RefreshSteps';
import { InfoLink } from '../help';
import Select from '@cloudscape-design/components/select';
import { useTagFilter } from '../components/TagFilterProvider';
import { tagKeys, exposureByTagValue, addFilter, NOT_TAGGED } from '../tag-filter';

// sessionStorage key for the in-flight refresh execution ARN. The pipeline
// runs server-side as a Lambda durable execution; this only lets the UI
// re-attach to it after a page navigation or reload.
const REFRESH_ARN_KEY = 'lifecycle-refresh-execution-arn';

// KPI figure: resources behind the rows; sub-line: distinct versions (same version
// in two accounts or regions counts once)
const total = (rows: DeprecationItem[]) =>
  rows.reduce((n, r) => n + (Number(r.service_specific?.total_affected) || 0), 0);
const versions = (rows: DeprecationItem[]) => {
  const n = distinctVersions(rows);
  return `${n} version${n === 1 ? '' : 's'}`;
};

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
      setProgress(execution.progress?.phases ? execution.progress : null);
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
    // same buckets as the My resources horizon scopes, so each KPI links to what it counts
    const byUrgency = { past: [] as DeprecationItem[], soon: [] as DeprecationItem[], year: [] as DeprecationItem[], later: [] as DeprecationItem[] };
    for (const r of concerns) {
      const b = exposureBucket(r);
      if (b !== 'fine') byUrgency[b].push(r);
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

  // Per-account breakdown (#144): attention + Extended Support money, shown only with several accounts
  const byAccount = useMemo(() => {
    const m = new Map<string, { name: string; rows: number; resources: number; concerns: number; worst: number; monthly: number; forecast: number }>();
    for (const r of inventory) {
      if (!r.account_id) continue;
      const e = m.get(r.account_id) || { name: r.account_name || '', rows: 0, resources: 0, concerns: 0, worst: 99, monthly: 0, forecast: 0 };
      e.name = e.name || r.account_name || '';
      e.rows += 1;
      e.resources += Number(r.service_specific?.total_affected) || 0;
      if (isConcern(r.status)) e.concerns += 1;
      e.worst = Math.min(e.worst, statusMeta(r.status).rank);
      const c = costExposure(r);
      if (c) { e.monthly += c.monthly; e.forecast += c.forecast_12m; }
      m.set(r.account_id, e);
    }
    // accounts scanned without any resource still count as covered
    for (const id of coverage?.accounts?.accounts_scanned || []) {
      if (!m.has(id)) m.set(id, { name: coverage?.accounts?.accounts.find((a) => a.id === id)?.name || '', rows: 0, resources: 0, concerns: 0, worst: 99, monthly: 0, forecast: 0 });
    }
    return [...m.entries()].sort((a, b) => a[1].worst - b[1].worst || b[1].concerns - a[1].concerns || b[1].forecast - a[1].forecast);
  }, [inventory, coverage]);
  const multiAccount = byAccount.length > 1;

  // By tag (#164): keys seen by the scan, exposure per value of the chosen key
  const tagFilter = useTagFilter();
  const keys = useMemo(() => tagKeys(inventory), [inventory]);
  const [tagKeyChoice, setTagKey] = useState('');
  const tagKey = tagKeyChoice || keys[0]?.key || '';
  const byTag = useMemo(() => (tagKey ? exposureByTagValue(inventory, tagKey, isConcern) : []), [inventory, tagKey]);

  const lastFactsRefresh = useMemo(
    () => facts.reduce<string | null>((max, f) => (!max || f.last_verified > max ? f.last_verified : max), null),
    [facts]);
  const factServices = useMemo(() => new Set(facts.map((f) => f.service_name)).size, [facts]);

  // RDS/Aurora Extended Support surcharge across the inventory (#142), split by
  // when it starts so a 2031 bill is never added to a 2027 one
  const money = useMemo(() => costTimeline(inventory), [inventory]);
  // Short line under the figure; the full breakdown sits in a popover on it
  const moneySub = useMemo(() => {
    // what comes next, in dollars, with the month it starts
    const short = money.within12
      ? `+${formatUsd(money.monthlyWithin12)}/month from ${formatMonth(money.nextStart)} · ${formatUsd(money.forecast12)} over 12 months`
      : money.later
        ? `next start ${formatMonth(money.nextStart)} (+${formatUsd(money.monthlyLater)}/month) · ${formatUsd(money.forecast12)} over 12 months`
        : `nothing more coming · ${formatUsd(money.forecast12)} over 12 months`;
    const lines = [
      money.now ? `${money.now} billing now: ${formatUsd(money.monthlyNow)}/month` : '',
      money.within12 ? `${money.within12} start within 12 months: +${formatUsd(money.monthlyWithin12)}/month${money.nextStart ? `, first in ${formatMonth(money.nextStart)}` : ''}` : '',
      money.later ? `${money.later} later (${formatUsd(money.monthlyLater)}/month once in Extended Support)` : '',
      'List prices, always-on at current size.',
    ].filter(Boolean);
    return (
      <Popover dismissButton={false} position="bottom" size="medium" triggerType="text" header="How the figure is built"
        content={<SpaceBetween size="xxs">{lines.map((l, i) => <Box key={i} variant="small">{l}</Box>)}</SpaceBetween>}>
        {short}
      </Popover>
    );
  }, [money]);

  if (loading) {
    return (
      <Container>
        <Box textAlign="center" padding="xxl">
          <StatusIndicator type="loading">Loading your exposure...</StatusIndicator>
        </Box>
      </Container>
    );
  }

  // One KPI = one key-value pair: label, the figure (a link into My resources), one line of context
  const kpi = (label: string, value: number | string, sub: React.ReactNode, color?: 'text-status-error' | 'text-status-warning' | 'text-status-success' | 'text-status-info', onClick?: () => void) => ({
    label,
    value: (
      <SpaceBetween size="xs">
        <Box fontSize="display-l" fontWeight="bold" color={color}>
          {onClick ? <Link onFollow={(e) => { e.preventDefault(); onClick(); }} fontSize="display-l" href="#" ariaLabel={`${label}: ${value}, open in My resources`}>{value}</Link> : value}
        </Box>
        <Box variant="small" color="text-body-secondary">{sub}</Box>
      </SpaceBetween>
    ),
  });

  const goResources = (status?: string) => navigate(status ? `/resources?status=${status}` : '/resources');

  return (
    <ContentLayout
      header={
          <Header
            variant="h1"
            description={`Resources in ${multiAccount ? 'your accounts' : 'this account'} running versions that AWS is retiring, matched against the deprecation facts published in the AWS documentation.`}
            info={<InfoLink />}
            actions={
              <Button variant="primary" iconName="refresh" loading={refreshing} onClick={handleRefresh} disabled={refreshing}>
                {refreshing ? 'Refreshing' : 'Refresh'}
              </Button>
            }
          >
            My exposure
          </Header>
      }
    >
    <SpaceBetween size="l">
      <Flashbar items={flashbarItems} stackItems />

      {progress?.phases && <RefreshSteps phases={progress.phases} running={refreshing} />}

      <Container header={<Header variant="h2">Summary</Header>}>
        <SpaceBetween size="l">
          <KeyValuePairs
            columns={money.priced ? 5 : 4}
            ariaLabel="Exposure summary"
            items={[
              kpi(EXPOSURE_BUCKETS.past, total(exposure.byUrgency.past), `${versions(exposure.byUrgency.past)}, act now`, 'text-status-error', () => goResources('past')),
              kpi(EXPOSURE_BUCKETS.soon, total(exposure.byUrgency.soon), `${versions(exposure.byUrgency.soon)}, plan the upgrade`, 'text-status-error', () => goResources('soon')),
              kpi(EXPOSURE_BUCKETS.year, total(exposure.byUrgency.year), `${versions(exposure.byUrgency.year)}, schedule it`, 'text-status-warning', () => goResources('year')),
              kpi(EXPOSURE_BUCKETS.fine, total(exposure.fine), `${versions(exposure.fine)} supported or unmatched`, 'text-status-success', () => goResources('fine')),
              // headline = what Extended Support bills today; sub-line = what is coming
              ...(money.priced > 0 ? [kpi('Extended Support now', `${formatUsd(money.monthlyNow)}/month`, moneySub,
                money.now ? 'text-status-error' : money.within12 ? 'text-status-warning' : 'text-status-success', () => goResources('cost'))] : []),
            ]}
          />

          <KeyValuePairs
            columns={2}
            ariaLabel="Data freshness"
            items={[
              {
                label: 'Account scan',
                value: (
                  <StatusIndicator type={coverage?.last_scan.last_verified ? 'success' : 'pending'}>
                    {coverage?.last_scan.resources ?? 0} resource groups across {byService.length} services
                    {multiAccount ? ` and ${byAccount.length} accounts` : ''}
                    {coverage?.last_scan.regions.length ? ` in ${coverage.last_scan.regions.join(', ')}` : ''}, {relative(coverage?.last_scan.last_verified)}
                    {coverage?.accounts?.accounts_failed?.length ? ` (${coverage.accounts.accounts_failed.length} account${coverage.accounts.accounts_failed.length === 1 ? '' : 's'} unreachable)` : ''}
                  </StatusIndicator>
                ),
              },
              {
                label: 'Catalog',
                value: (
                  <StatusIndicator type={facts.length ? 'success' : 'pending'}>
                    {facts.length} facts across {factServices} services, refreshed {relative(lastFactsRefresh)}
                  </StatusIndicator>
                ),
              },
            ]}
          />
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
                  ...(multiAccount ? [{ id: 'account', header: 'Account', cell: (r: DeprecationItem) => r.account_name || r.account_id || '-' }] : []),
                  { id: 'region', header: 'Region', cell: (r) => r.region || '-' },
                ]}
                footer={exposure.concerns.length > deadlines.length && (
                  <Box textAlign="center"><Link onFollow={(e) => { e.preventDefault(); goResources(); }} href="#">See all my deadlines</Link></Box>
                )}
              />
            ),
          },
          {
            id: 'services',
            label: `By service (${byService.length})`,
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
          ...(multiAccount ? [{
            id: 'accounts',
            label: `By account (${byAccount.length})`,
            content: (
              <Table
                variant="embedded"
                items={byAccount}
                trackBy={([k]) => k}
                columnDefinitions={[
                  { id: 'account', header: 'Account', cell: ([k, v]) => (
                    <SpaceBetween size="xxxs">
                      <Link onFollow={(e) => { e.preventDefault(); navigate(`/resources?status=all&account=${k}`); }} href="#">{v.name || k}</Link>
                      {v.name && <Box variant="small" color="text-body-secondary">{k}</Box>}
                    </SpaceBetween>
                  ) },
                  { id: 'resources', header: 'Resources', cell: ([, v]) => v.resources },
                  { id: 'versions', header: 'Versions in use', cell: ([, v]) => v.rows },
                  { id: 'concerns', header: 'Need attention', cell: ([, v]) => v.concerns
                      ? <StatusIndicator type={v.worst <= 1 ? 'error' : 'warning'}>{v.concerns} of {v.rows}</StatusIndicator>
                      : <StatusIndicator type="success">none</StatusIndicator> },
                  { id: 'support', header: <SupportTierHeader />, cell: ([k]) => <SupportTierCell status={coverage?.health?.by_account?.[k]} /> },
                  { id: 'money', header: 'Extended Support', cell: ([, v]) => v.forecast || v.monthly
                      ? <SpaceBetween size="xxxs"><Box variant="strong">{formatUsd(v.monthly)}/mo</Box><Box variant="small" color="text-body-secondary">{formatUsd(v.forecast)} next 12 mo</Box></SpaceBetween>
                      : <Box color="text-body-secondary">-</Box> },
                ]}
              />
            ),
          }] : []),
          // Who carries the exposure (#164): one line per value of a tag key, not tagged first
          ...(keys.length ? [{
            id: 'tags',
            label: `By tag (${byTag.length})`,
            content: (
              <Table
                variant="embedded"
                items={byTag}
                trackBy="value"
                header={
                  <Header variant="h3" description="Resources grouped by the value of one tag key; the resources without it come first. Choose a value to focus the tracker on it."
                    actions={
                      <Select selectedOption={{ label: tagKey, value: tagKey }} onChange={({ detail }) => setTagKey(detail.selectedOption.value!)}
                        options={keys.map((k) => ({ label: k.key, value: k.key, description: `${k.resources} resource${k.resources === 1 ? '' : 's'}, ${k.distinct} value${k.distinct === 1 ? '' : 's'}` }))}
                        selectedAriaLabel="Selected" ariaLabel="Tag key" />
                    }>
                    Tag key
                  </Header>
                }
                columnDefinitions={[
                  { id: 'value', header: tagKey, cell: (v) => v.value === NOT_TAGGED
                      ? <Link onFollow={(e) => { e.preventDefault(); tagFilter.setFilters(addFilter(tagFilter.filters, tagKey, NOT_TAGGED)); }} href="#"><Box color="text-status-warning" display="inline">Not tagged</Box></Link>
                      : <Link onFollow={(e) => { e.preventDefault(); tagFilter.setFilters(addFilter(tagFilter.filters, tagKey, v.value)); }} href="#">{v.value}</Link> },
                  { id: 'resources', header: 'Resources', cell: (v) => v.resources },
                  { id: 'versions', header: 'Versions in use', cell: (v) => v.versions },
                  { id: 'attention', header: 'Need attention', cell: (v) => v.attention
                      ? <StatusIndicator type="warning">{v.attention} of {v.resources}</StatusIndicator>
                      : <StatusIndicator type="success">none</StatusIndicator> },
                  { id: 'money', header: 'Extended Support', cell: (v) => v.forecast_12m || v.monthly
                      ? <SpaceBetween size="xxxs"><Box variant="strong">{formatUsd(v.monthly)}/mo</Box><Box variant="small" color="text-body-secondary">{formatUsd(v.forecast_12m)} next 12 mo</Box></SpaceBetween>
                      : <Box color="text-body-secondary">-</Box> },
                ]}
              />
            ),
          }] : []),
        ]}
      />
    </SpaceBetween>
    </ContentLayout>
  );
}
