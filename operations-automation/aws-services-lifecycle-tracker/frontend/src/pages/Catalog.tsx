import { useState, useEffect, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import Table from '@cloudscape-design/components/table';
import Header from '@cloudscape-design/components/header';
import Box from '@cloudscape-design/components/box';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Badge from '@cloudscape-design/components/badge';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import TextFilter from '@cloudscape-design/components/text-filter';
import Select from '@cloudscape-design/components/select';
import Pagination from '@cloudscape-design/components/pagination';
import Toggle from '@cloudscape-design/components/toggle';
import Link from '@cloudscape-design/components/link';
import Alert from '@cloudscape-design/components/alert';
import { getLifecycleData, DeprecationItem } from '../api';
import { statusMeta, isConcern, getDeadline, formatDate, formatDaysLeft, urgencySort, serviceLabel, itemName, resourcesByFact, STATUS_META } from '../lifecycle';

const PAGE = 50;

const SCOPE_OPTIONS = [
  { label: 'Lifecycle concerns', value: 'concerns' },
  { label: 'All facts', value: 'all' },
  ...Object.entries(STATUS_META).filter(([k]) => k !== 'unknown').map(([value, m]) => ({ label: m.label, value })),
];

// Dates worth showing, in display order
const DATE_LABELS: Array<[string, string]> = [
  ['deprecation_date', 'Deprecated'],
  ['end_of_support_date', 'End of support'],
  ['end_of_standard_support_date', 'End of standard support'],
  ['end_of_extended_support_date', 'End of extended support'],
  ['retirement_date', 'Retired'],
  ['target_retirement_date', 'Target retirement'],
  ['block_create_date', 'Block create'],
  ['block_update_date', 'Block update'],
  ['end_of_life_date', 'End of life'],
];

export default function Catalog() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [facts, setFacts] = useState<DeprecationItem[]>([]);
  const [inventory, setInventory] = useState<DeprecationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filterText, setFilterText] = useState(params.get('q') || '');
  const [scope, setScope] = useState(params.get('status') || 'concerns');
  const [service, setService] = useState(params.get('service') || 'all');
  const [onlyMine, setOnlyMine] = useState(params.get('mine') === '1');
  const [page, setPage] = useState(1);

  useEffect(() => {
    (async () => {
      try {
        const data = await getLifecycleData();
        setFacts(data.facts);
        setInventory(data.inventory);
      } catch (err: any) {
        setError(`Failed to load the catalog: ${err.message}`);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const mine = useMemo(() => resourcesByFact(inventory), [inventory]);
  const services = useMemo(() => [...new Set(facts.map((f) => f.service_name))].sort(), [facts]);

  const filtered = useMemo(() => {
    let out = [...facts];
    if (scope === 'concerns') out = out.filter((f) => isConcern(f.status));
    else if (scope !== 'all') out = out.filter((f) => f.status === scope);
    if (service !== 'all') out = out.filter((f) => f.service_name === service);
    if (onlyMine) out = out.filter((f) => mine.has(`${f.service_name}|${f.item_id}`));
    if (filterText) {
      const q = filterText.toLowerCase();
      out = out.filter((f) => `${f.service_name} ${serviceLabel(f.service_name)} ${f.item_id} ${JSON.stringify(f.service_specific)}`.toLowerCase().includes(q));
    }
    return out.sort(urgencySort);
  }, [facts, scope, service, onlyMine, filterText, mine]);

  useEffect(() => { setPage(1); }, [scope, service, onlyMine, filterText]);

  const updateParams = (next: Record<string, string>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) { if (v && v !== 'all' && v !== 'concerns' && v !== '0') p.set(k, v); else p.delete(k); }
    setParams(p, { replace: true });
  };

  const inMyAccount = facts.filter((f) => mine.has(`${f.service_name}|${f.item_id}`)).length;
  const pageItems = filtered.slice((page - 1) * PAGE, page * PAGE);

  return (
    <SpaceBetween size="l">
      {error && <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>}

      <Table
        items={pageItems}
        trackBy="item_id"
        loading={loading}
        loadingText="Loading the catalog..."
        variant="full-page"
        stickyHeader
        header={
          <Header
            variant="h1"
            counter={`(${filtered.length})`}
            description={`Deprecation facts extracted from the AWS documentation: ${facts.length} across ${services.length} services, ${inMyAccount} of them matching something in your account.`}
          >
            Catalog
          </Header>
        }
        filter={
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center' }}>
            <TextFilter filteringText={filterText} filteringPlaceholder="Search versions, runtimes, engines..."
              filteringAriaLabel="Filter catalog"
              onChange={({ detail }) => { setFilterText(detail.filteringText); updateParams({ q: detail.filteringText }); }} />
            <Select selectedOption={SCOPE_OPTIONS.find((o) => o.value === scope) || SCOPE_OPTIONS[0]}
              onChange={({ detail }) => { setScope(detail.selectedOption.value!); updateParams({ status: detail.selectedOption.value! }); }}
              options={SCOPE_OPTIONS} selectedAriaLabel="Selected" />
            <Select selectedOption={{ label: service === 'all' ? 'All services' : serviceLabel(service), value: service }}
              onChange={({ detail }) => { setService(detail.selectedOption.value!); updateParams({ service: detail.selectedOption.value! }); }}
              options={[{ label: 'All services', value: 'all' }, ...services.map((s) => ({ label: serviceLabel(s), value: s }))]}
              selectedAriaLabel="Selected" />
            <Toggle checked={onlyMine} onChange={({ detail }) => { setOnlyMine(detail.checked); updateParams({ mine: detail.checked ? '1' : '0' }); }}>
              Only what I run
            </Toggle>
          </div>
        }
        pagination={<Pagination currentPageIndex={page} pagesCount={Math.max(1, Math.ceil(filtered.length / PAGE))} onChange={({ detail }) => setPage(detail.currentPageIndex)} />}
        empty={
          <Box textAlign="center" padding="l" color="text-body-secondary">
            <Box variant="strong">{facts.length === 0 ? 'The catalog is empty' : 'No facts match these filters'}</Box>
            <Box variant="p">{facts.length === 0 ? 'Click Refresh on the dashboard to extract the deprecation facts.' : ''}</Box>
          </Box>
        }
        columnDefinitions={[
          { id: 'service', header: 'Service', cell: (f) => <Badge color="blue">{serviceLabel(f.service_name)}</Badge> },
          {
            id: 'name', header: 'Version', cell: (f) => (
              <SpaceBetween size="xxxs">
                <Box variant="strong">{itemName(f)}</Box>
                <Box variant="small" color="text-body-secondary">{f.service_specific?.identifier}</Box>
              </SpaceBetween>
            ),
          },
          { id: 'status', header: 'Status', cell: (f) => <StatusIndicator type={statusMeta(f.status).indicator}>{statusMeta(f.status).label}</StatusIndicator> },
          {
            id: 'deadline', header: 'Next deadline', cell: (f) => {
              const d = getDeadline(f);
              return d ? <SpaceBetween size="xxxs"><Box>{formatDate(d.date)}</Box><Box variant="small" color="text-body-secondary">{formatDaysLeft(d)}</Box></SpaceBetween> : <Box color="text-body-secondary">-</Box>;
            },
          },
          {
            id: 'dates', header: 'Key dates', cell: (f) => {
              const rows = DATE_LABELS.filter(([k]) => f.service_specific?.[k] && f.service_specific[k] !== 'N/A');
              return rows.length
                ? <SpaceBetween size="xxxs">{rows.map(([k, label]) => <Box key={k} variant="small"><strong>{label}:</strong> {String(f.service_specific[k])}</Box>)}</SpaceBetween>
                : <Box variant="small" color="text-body-secondary">-</Box>;
            },
          },
          {
            id: 'mine', header: 'In my account', cell: (f) => {
              const rows = mine.get(`${f.service_name}|${f.item_id}`) || [];
              const n = rows.reduce((s, r) => s + (Number(r.service_specific?.total_affected) || 0), 0);
              return n
                ? <Link onFollow={(e) => { e.preventDefault(); navigate(`/resources?q=${encodeURIComponent(rows[0].service_specific?.identifier || '')}&status=all`); }} href="#">
                    <StatusIndicator type={isConcern(f.status) ? 'warning' : 'info'}>{n} resource{n === 1 ? '' : 's'}</StatusIndicator>
                  </Link>
                : <Box variant="small" color="text-body-secondary">none</Box>;
            },
          },
          { id: 'source', header: 'Source', cell: (f) => f.source_url ? <Link href={f.source_url} external fontSize="body-s">docs</Link> : '-' },
        ]}
      />
    </SpaceBetween>
  );
}
