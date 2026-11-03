import { useState, useEffect, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Select from '@cloudscape-design/components/select';
import TextFilter from '@cloudscape-design/components/text-filter';
import Steps, { StepsProps } from '@cloudscape-design/components/steps';
import Alert from '@cloudscape-design/components/alert';
import Link from '@cloudscape-design/components/link';
import { getLifecycleData, DeprecationItem } from '../api';
import { statusMeta, serviceLabel, itemName, formatDate, isInventory, resourceCount, resourceWord, accountLabel, accountsIn } from '../lifecycle';
import { InfoLink } from '../help';

// Dates that mark a deadline (in the order they are listed per item)
const MILESTONES: Array<[string, string]> = [
  ['deprecation_date', 'deprecation'],
  ['end_of_standard_support_date', 'end of standard support'],
  ['end_of_support_date', 'end of support'],
  ['retirement_date', 'retirement'],
  ['target_retirement_date', 'target retirement'],
  ['block_update_date', 'function updates blocked'],
  ['block_create_date', 'function creation blocked'],
  ['end_of_extended_support_date', 'end of extended support'],
];

// One version + one deadline. In the "mine" lens the same version can run in
// several accounts/regions: those rows are merged into one milestone and the
// per-scope breakdown is listed underneath (#150 item 4).
interface Milestone { item: DeprecationItem; items: DeprecationItem[]; label: string; date: Date; days: number }

const scopeLabel = (r: DeprecationItem, multiAccount: boolean): string =>
  multiAccount && r.account_id ? `${accountLabel(r.account_id, r.account_name)}${r.region ? `, ${r.region}` : ''}` : (r.region || '');

const bucketOf = (days: number) =>
  days <= 0 ? 'Passed' : days <= 90 ? 'Next 90 days' : days <= 180 ? '3 to 6 months' : days <= 365 ? '6 to 12 months' : 'Later';
const BUCKETS = ['Passed', 'Next 90 days', '3 to 6 months', '6 to 12 months', 'Later'];
const bucketType = (b: string) => (b === 'Passed' || b === 'Next 90 days' ? 'error' : b === '3 to 6 months' ? 'warning' : 'info') as 'error' | 'warning' | 'info';
// Icon of one milestone = icon of its horizon bucket (same set as StatusIndicator)
const milestoneType = (days: number): StepsProps.Status => bucketType(bucketOf(days));

const HORIZON_OPTIONS = [{ label: 'Any date', value: 'all' }, ...BUCKETS.map((b) => ({ label: b, value: b }))];

export default function Timeline() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [inventory, setInventory] = useState<DeprecationItem[]>([]);
  const [facts, setFacts] = useState<DeprecationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  // Filters, mirrored in the URL like the other pages (q, service, account)
  const lens = (params.get('lens') === 'catalog' ? 'catalog' : 'mine') as 'mine' | 'catalog';
  const filterText = params.get('q') || '';
  const service = params.get('service') || 'all';
  const account = params.get('account') || 'all';
  const horizon = params.get('horizon') || 'all';
  const updateParams = (next: Record<string, string>) => setParams((prev) => {
    const p = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(next)) { if (v && v !== 'all' && v !== 'mine') p.set(k, v); else p.delete(k); }
    return p;
  }, { replace: true });

  useEffect(() => {
    (async () => {
      try {
        const data = await getLifecycleData();
        setInventory(data.inventory);
        setFacts(data.facts);
      } catch (err: any) {
        setError(`Failed to load timeline: ${err.message}`);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const milestones = useMemo(() => {
    const now = Date.now();
    const source = lens === 'mine' ? inventory : facts;
    const out: Milestone[] = [];
    for (const item of source) {
      for (const [field, label] of MILESTONES) {
        const raw = item.service_specific?.[field];
        if (!raw || typeof raw !== 'string' || /^(n\/a|tbd|-)$/i.test(raw)) continue;
        const d = new Date(raw);
        if (isNaN(d.getTime())) continue;
        const days = Math.ceil((d.getTime() - now) / 86_400_000);
        // Catalog: only upcoming dates (the past is history). Mine: also show
        // what already passed - those are the resources in trouble today.
        if (lens === 'catalog' && days < 0) continue;
        if (lens === 'mine' && days < -365) continue;
        out.push({ item, items: [item], label, date: d, days });
      }
    }
    if (lens === 'mine') {
      // same service + version + milestone + date across accounts/regions = one entry
      const merged = new Map<string, Milestone>();
      for (const m of out) {
        const key = `${m.item.service_name}|${m.item.service_specific?.identifier || itemName(m.item)}|${m.label}|${m.date.toISOString()}`;
        const e = merged.get(key);
        if (e) e.items.push(m.item); else merged.set(key, m);
      }
      return [...merged.values()].sort((a, b) => a.days - b.days);
    }
    return out.sort((a, b) => a.days - b.days);
  }, [inventory, facts, lens]);
  const accounts = useMemo(() => accountsIn(inventory), [inventory]);
  const multiAccount = accounts.length > 1;
  const services = useMemo(() => [...new Set((lens === 'mine' ? inventory : facts).map((r) => r.service_name))].sort(), [inventory, facts, lens]);

  const filtered = useMemo(() => {
    const q = filterText.trim().toLowerCase();
    return milestones.filter((m) =>
      (service === 'all' || m.item.service_name === service)
      && (horizon === 'all' || bucketOf(m.days) === horizon)
      && (account === 'all' || lens !== 'mine' || m.items.some((r) => r.account_id === account))
      && (!q || `${m.item.service_name} ${serviceLabel(m.item.service_name)} ${itemName(m.item)} ${m.label} ${m.items.map((r) => `${r.region || ''} ${r.account_id || ''} ${r.account_name || ''}`).join(' ')}`.toLowerCase().includes(q)));
  }, [milestones, filterText, service, horizon, account, lens]);

  const grouped = useMemo(() => {
    const g = new Map<string, Milestone[]>();
    for (const m of filtered) g.set(bucketOf(m.days), [...(g.get(bucketOf(m.days)) || []), m]);
    return BUCKETS.filter((b) => g.has(b)).map((b) => [b, g.get(b)!] as const);
  }, [filtered]);
  const counter = filtered.length === milestones.length ? `(${milestones.length})` : `(${filtered.length} of ${milestones.length})`;

  if (loading) {
    return <Container><Box textAlign="center" padding="xxl"><StatusIndicator type="loading">Loading timeline...</StatusIndicator></Box></Container>;
  }

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          info={<InfoLink />}
          counter={counter}
          description={lens === 'mine'
            ? 'Deadlines for the versions running in your accounts, soonest first; a version running in several accounts or regions is one entry. Dates that already passed are shown too - those resources are the ones in trouble today.'
            : 'Upcoming deadlines across the whole catalog, whether or not you run the version.'}
          actions={
            <SegmentedControl
              selectedId={lens}
              onChange={({ detail }) => updateParams({ lens: detail.selectedId, account: '' })}
              options={[{ text: 'My resources', id: 'mine' }, { text: 'Whole catalog', id: 'catalog' }]}
            />
          }
        >
          {lens === 'mine' ? 'Deadlines for my resources' : 'Deadlines in the catalog'}
        </Header>
      }
    >
    <SpaceBetween size="l">
      {error && <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>}

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
        <TextFilter filteringText={filterText} filteringPlaceholder="Search versions, deadlines, regions..." filteringAriaLabel="Filter deadlines"
          countText={`${filtered.length} match${filtered.length === 1 ? '' : 'es'}`}
          onChange={({ detail }) => updateParams({ q: detail.filteringText })} />
        <Select selectedOption={{ label: service === 'all' ? 'All services' : serviceLabel(service), value: service }}
          onChange={({ detail }) => updateParams({ service: detail.selectedOption.value! })}
          options={[{ label: 'All services', value: 'all' }, ...services.map((s) => ({ label: serviceLabel(s), value: s }))]}
          selectedAriaLabel="Selected" />
        <Select selectedOption={HORIZON_OPTIONS.find((o) => o.value === horizon) || HORIZON_OPTIONS[0]}
          onChange={({ detail }) => updateParams({ horizon: detail.selectedOption.value! })}
          options={HORIZON_OPTIONS} selectedAriaLabel="Selected" />
        {multiAccount && lens === 'mine' && (
          <Select selectedOption={{ label: account === 'all' ? 'All accounts' : accountLabel(account, accounts.find((a) => a.id === account)?.name), value: account }}
            onChange={({ detail }) => updateParams({ account: detail.selectedOption.value! })}
            options={[{ label: 'All accounts', value: 'all' }, ...accounts.map((a) => ({ label: accountLabel(a.id, a.name), value: a.id }))]}
            selectedAriaLabel="Selected" />
        )}
      </div>

      {filtered.length === 0 ? (
        <Container>
          <Box textAlign="center" color="text-body-secondary" padding="xxl">
            <Box variant="strong">{milestones.length === 0 ? (lens === 'mine' ? 'Nothing in your accounts has a deadline' : 'No upcoming deadlines in the catalog') : 'No deadlines match these filters'}</Box>
            {milestones.length > 0 && <Box variant="p"><Link onFollow={(e) => { e.preventDefault(); updateParams({ q: '', service: '', horizon: '', account: '' }); }} href="#">Clear filters</Link></Box>}
          </Box>
        </Container>
      ) : (
          <SpaceBetween size="l">
            {grouped.map(([bucket, list]) => (
              <Container
                key={bucket}
                header={
                  <Header variant="h2" counter={`(${list.length})`}>
                    <StatusIndicator type={bucketType(bucket)}>{bucket}</StatusIndicator>
                  </Header>
                }
              >
                <Steps
                  ariaLabel={`${bucket} deadlines`}
                  steps={list.map((m): StepsProps.Step => {
                    const st = statusMeta(m.item.status);
                    const mine = isInventory(m.item);
                    const total = m.items.reduce((n, r) => n + resourceCount(r), 0);
                    const single = m.items.length === 1;
                    const target = single
                      ? `/resources?status=all&details=${encodeURIComponent(m.item.item_id)}`
                      : `/resources?status=all&q=${encodeURIComponent(m.item.service_specific?.identifier || itemName(m.item))}`;
                    const when = m.days < 0 ? `${-m.days} days ago` : m.days === 0 ? 'today' : `in ${m.days} days`;
                    const where = mine
                      ? (single
                          ? `${total} ${resourceWord(total)}${scopeLabel(m.item, multiAccount) ? ` in ${scopeLabel(m.item, multiAccount)}` : ''}`
                          : `${total} ${resourceWord(total)}: ${m.items.map((r) => `${resourceCount(r)} in ${scopeLabel(r, multiAccount) || 'this account'}`).join(', ')}`)
                      : '';
                    const title = `${serviceLabel(m.item.service_name)} ${itemName(m.item)}: ${m.label}`;
                    return {
                      status: milestoneType(m.days),
                      statusIconAriaLabel: m.days < 0 ? 'Passed' : m.days <= 90 ? 'Within 90 days' : m.days <= 180 ? 'Within 6 months' : m.days <= 365 ? 'Within a year' : 'Later',
                      // annotation = the timestamp (component guideline), one line
                      annotation: formatDate(m.date),
                      // header = brief summary; a link to the resources when they are mine
                      header: mine
                        ? <Link onFollow={(e) => { e.preventDefault(); navigate(target); }} href="#">{title}</Link>
                        : title,
                      // details = one line of context
                      details: (
                        <Box variant="small" color="text-body-secondary">
                          {when} · {st.label}{where ? ` · ${where}` : ''}
                        </Box>
                      ),
                    };
                  })}
                />
              </Container>
            ))}
          </SpaceBetween>
        )}
    </SpaceBetween>
    </ContentLayout>
  );
}
