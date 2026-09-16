import { useState, useEffect, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import Table from '@cloudscape-design/components/table';
import Header from '@cloudscape-design/components/header';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Badge from '@cloudscape-design/components/badge';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import TextFilter from '@cloudscape-design/components/text-filter';
import Select from '@cloudscape-design/components/select';
import Pagination from '@cloudscape-design/components/pagination';
import CollectionPreferences, { CollectionPreferencesProps } from '@cloudscape-design/components/collection-preferences';
import { useCollection } from '@cloudscape-design/collection-hooks';
import Toggle from '@cloudscape-design/components/toggle';
import Link from '@cloudscape-design/components/link';
import Alert from '@cloudscape-design/components/alert';
import { getLifecycleData, DeprecationItem } from '../api';
import { statusMeta, isConcern, getDeadline, formatDate, formatDaysLeft, urgencySort, serviceLabel, itemName, resourcesByFact, STATUS_META } from '../lifecycle';
import { InfoLink } from '../help';
import GenAiLabel from '../components/GenAiLabel';

// Table preferences (page size, visible columns); kept per browser
const PREFS_KEY = 'lifecycle-catalog-preferences';
const DEFAULT_PREFS: CollectionPreferencesProps.Preferences = { pageSize: 50, contentDisplay: [
  { id: 'service', visible: true }, { id: 'name', visible: true }, { id: 'status', visible: true }, { id: 'deadline', visible: true },
  { id: 'dates', visible: true }, { id: 'mine', visible: true }, { id: 'source', visible: true },
] };
const loadPrefs = (): CollectionPreferencesProps.Preferences => {
  try { return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') }; } catch { return DEFAULT_PREFS; }
};

// Scope dropdown: two buckets first, then one entry per status in a group
const SCOPE_OPTIONS = [
  { label: 'Needs attention', value: 'concerns', description: 'End of life, deprecated, past standard support or ending within a year' },
  { label: 'Everything', value: 'all', description: 'Including versions still supported' },
  {
    label: 'By status',
    options: Object.entries(STATUS_META).filter(([k]) => k !== 'unknown').map(([value, m]) => ({ label: m.label, value })),
  },
];
const FLAT_SCOPE_OPTIONS: { label: string; value: string }[] = SCOPE_OPTIONS.flatMap((o) => ('options' in o && o.options ? o.options : [o as { label: string; value: string }]));

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
  const [preferences, setPreferences] = useState<CollectionPreferencesProps.Preferences>(loadPrefs);

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

  // Scope / service / mine narrow the source; text search, sorting and paging
  // are the collection hooks' job (one place, consistent counts, page resets).
  const scoped = useMemo(() => {
    let out = facts;
    if (scope === 'concerns') out = out.filter((f) => isConcern(f.status));
    else if (scope !== 'all') out = out.filter((f) => f.status === scope);
    if (service !== 'all') out = out.filter((f) => f.service_name === service);
    if (onlyMine) out = out.filter((f) => mine.has(`${f.service_name}|${f.item_id}`));
    return out;
  }, [facts, scope, service, onlyMine, mine]);

  const { items, filteredItemsCount, collectionProps, filterProps, paginationProps } = useCollection(scoped, {
    filtering: {
      defaultFilteringText: filterText,
      filteringFunction: (f, text) =>
        `${f.service_name} ${serviceLabel(f.service_name)} ${f.item_id} ${JSON.stringify(f.service_specific)}`.toLowerCase().includes(text.toLowerCase()),
    },
    sorting: { defaultState: { sortingColumn: { sortingComparator: urgencySort } } },
    pagination: { pageSize: preferences.pageSize },
  });
  const filtered = filteredItemsCount ?? items.length;

  const updateParams = (next: Record<string, string>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) { if (v && v !== 'all' && v !== 'concerns' && v !== '0') p.set(k, v); else p.delete(k); }
    setParams(p, { replace: true });
  };

  const inMyAccount = facts.filter((f) => mine.has(`${f.service_name}|${f.item_id}`)).length;

  return (
    <SpaceBetween size="l">
      {error && <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>}

      <Table
        {...collectionProps}
        items={items}
        trackBy="item_id"
        columnDisplay={preferences.contentDisplay}
        loading={loading}
        loadingText="Loading the catalog..."
        variant="full-page"
        stickyHeader
        header={
          <Header
            variant="h1"
            info={<InfoLink />}
            counter={filtered === facts.length ? `(${facts.length})` : `(${filtered} of ${facts.length})`}
            description={
              <SpaceBetween size="xxs">
                <GenAiLabel text="Generated by AI from the AWS documentation, each row links to its source page" />
                <span>{`${facts.length} version facts across ${services.length} services (${facts.filter((f) => isConcern(f.status)).length} need attention, ${facts.length - facts.filter((f) => isConcern(f.status)).length} still supported), ${inMyAccount} of them matching something in your accounts. The list shows the current scope and filters.`}</span>
              </SpaceBetween>
            }
          >
            Catalog
          </Header>
        }
        filter={
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center' }}>
            <TextFilter {...filterProps} filteringPlaceholder="Search versions, runtimes, engines"
              filteringAriaLabel="Filter catalog" countText={`${filtered} match${filtered === 1 ? '' : 'es'}`}
              onChange={(e) => { filterProps.onChange(e); setFilterText(e.detail.filteringText); updateParams({ q: e.detail.filteringText }); }} />
            <Select selectedOption={FLAT_SCOPE_OPTIONS.find((o) => o.value === scope) || FLAT_SCOPE_OPTIONS[0]}
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
        pagination={<Pagination {...paginationProps} />}
        preferences={
          <CollectionPreferences
            title="Preferences"
            confirmLabel="Confirm"
            cancelLabel="Cancel"
            preferences={preferences}
            onConfirm={({ detail }) => { setPreferences(detail); localStorage.setItem(PREFS_KEY, JSON.stringify(detail)); }}
            pageSizePreference={{ title: 'Page size', options: [{ value: 25, label: '25 facts' }, { value: 50, label: '50 facts' }, { value: 100, label: '100 facts' }] }}
            contentDisplayPreference={{ title: 'Columns', options: [
              { id: 'service', label: 'Service', alwaysVisible: true }, { id: 'name', label: 'Version', alwaysVisible: true },
              { id: 'status', label: 'Status' }, { id: 'deadline', label: 'Next deadline' }, { id: 'dates', label: 'Key dates' },
              { id: 'mine', label: 'In my account' }, { id: 'source', label: 'Source' },
            ] }}
          />
        }
        empty={
          <Box textAlign="center" padding="l" color="text-body-secondary">
            <SpaceBetween size="xs">
              <Box variant="strong">{facts.length === 0 ? 'The catalog is empty' : 'No facts match these filters'}</Box>
              <Box variant="p">{facts.length === 0 ? 'Choose Refresh on My exposure to extract the deprecation facts.' : 'Clear the search or widen the scope.'}</Box>
              {facts.length === 0
                ? <Button onClick={() => navigate('/dashboard')}>Go to My exposure</Button>
                : <Button onClick={() => {
                    filterProps.onChange({ detail: { filteringText: '' } } as any);
                    setFilterText(''); setScope('all'); setService('all'); setOnlyMine(false);
                    updateParams({ q: '', status: 'all', service: 'all', mine: '0' });
                  }}>Clear filters</Button>}
            </SpaceBetween>
          </Box>
        }
        columnDefinitions={[
          { id: 'service', header: 'Service', cell: (f) => <Badge color="blue">{serviceLabel(f.service_name)}</Badge>, sortingField: 'service_name' },
          {
            id: 'name', header: 'Version', cell: (f) => (
              <SpaceBetween size="xxxs">
                <Box variant="strong">{itemName(f)}</Box>
                <Box variant="small" color="text-body-secondary">{f.service_specific?.identifier}</Box>
              </SpaceBetween>
            ),
          },
          { id: 'status', header: 'Status', cell: (f) => <StatusIndicator type={statusMeta(f.status).indicator}>{statusMeta(f.status).label}</StatusIndicator>,
            sortingComparator: (a, b) => statusMeta(a.status).rank - statusMeta(b.status).rank },
          {
            id: 'deadline', header: 'Next deadline', sortingComparator: urgencySort, cell: (f) => {
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
                ? <Link onFollow={(e) => {
                    e.preventDefault();
                    // one region -> open its details directly; several -> filtered list
                    navigate(rows.length === 1
                      ? `/resources?status=all&details=${encodeURIComponent(rows[0].item_id)}`
                      : `/resources?q=${encodeURIComponent(rows[0].service_specific?.identifier || '')}&status=all`);
                  }} href="#">
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
