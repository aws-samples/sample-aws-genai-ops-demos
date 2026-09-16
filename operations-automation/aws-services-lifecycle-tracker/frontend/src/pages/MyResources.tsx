import { useState, useEffect, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import Table, { TableProps } from '@cloudscape-design/components/table';
import Header from '@cloudscape-design/components/header';
import Box from '@cloudscape-design/components/box';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Badge from '@cloudscape-design/components/badge';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import PropertyFilter, { PropertyFilterProps } from '@cloudscape-design/components/property-filter';
import CollectionPreferences, { CollectionPreferencesProps } from '@cloudscape-design/components/collection-preferences';
import Pagination from '@cloudscape-design/components/pagination';
import { useCollection, PropertyFilterQuery, PropertyFilterToken } from '@cloudscape-design/collection-hooks';
import Select from '@cloudscape-design/components/select';
import Button from '@cloudscape-design/components/button';
import Modal from '@cloudscape-design/components/modal';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import DatePicker from '@cloudscape-design/components/date-picker';
import Textarea from '@cloudscape-design/components/textarea';
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar';
import Link from '@cloudscape-design/components/link';
import Popover from '@cloudscape-design/components/popover';
import { getLifecycleData, getActionPlans, createActionPlan, getScanners, DeprecationItem, ActionPlan, ScanCoverage } from '../api';
import {
  statusMeta, isConcern, getDeadline, formatDate, formatDaysLeft, urgencySort, serviceLabel, itemName, STATUS_META,
  resourceCount, resourceWord, healthFlagged, costExposure, formatUsd, accountsIn, accountLabel, costTimeline, formatMonth,
  exposureBucket, EXPOSURE_BUCKETS, ExposureBucket,
} from '../lifecycle';
import ResourceDetails, { resourceDetailsHeader } from '../components/ResourceDetails';
import { InfoLink } from '../help';
import { useSplitPanel } from '../split-panel';

// Scope dropdown: three buckets first, then one entry per status in a group
const SCOPE_OPTIONS = [
  { label: 'Needs attention', value: 'concerns', description: 'End of life, deprecated, past standard support or ending within a year' },
  { label: 'Everything found', value: 'all', description: 'Including supported and unmatched versions' },
  { label: 'Extended Support exposure', value: 'cost', description: 'RDS/Aurora versions billing or about to bill Extended Support' },
  // the four My exposure KPIs
  { label: 'By horizon', options: (['past', 'soon', 'year', 'later', 'fine'] as ExposureBucket[]).map((value) => ({ label: EXPOSURE_BUCKETS[value], value })) },
  { label: 'By status', options: Object.entries(STATUS_META).map(([value, m]) => ({ label: m.label, value })) },
];
const isBucket = (v: string): v is ExposureBucket => v in EXPOSURE_BUCKETS;
const FLAT_SCOPE_OPTIONS: { label: string; value: string }[] = SCOPE_OPTIONS.flatMap((o) => ('options' in o && o.options ? o.options : [o as { label: string; value: string }]));

// Table preferences (page size, visible columns); kept per browser
const PREFS_KEY = 'lifecycle-resources-preferences';
const DEFAULT_PREFS: CollectionPreferencesProps.Preferences = {
  pageSize: 50,
  contentDisplay: ['service', 'version', 'status', 'deadline', 'resources', 'cost', 'account', 'region', 'plan', 'verified'].map((id) => ({ id, visible: true })),
};
const loadPrefs = (): CollectionPreferencesProps.Preferences => {
  try { return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') }; } catch { return DEFAULT_PREFS; }
};

// Property filter: one free-text token plus Service / Region / Account properties.
// Tokens mirror the URL params q, service, region, account so deep links keep working.
const TOKEN_PARAMS: Record<string, string> = { service_name: 'service', region: 'region', account_id: 'account' };
const queryFromParams = (p: URLSearchParams): PropertyFilterQuery => {
  const tokens: PropertyFilterToken[] = [];
  if (p.get('q')) tokens.push({ operator: ':', value: p.get('q') });
  for (const [key, param] of Object.entries(TOKEN_PARAMS)) {
    const v = p.get(param);
    if (v && v !== 'all') tokens.push({ propertyKey: key, operator: '=', value: v });
  }
  return { operation: 'and', tokens };
};
const paramsFromQuery = (q: PropertyFilterQuery): Record<string, string> => {
  const out: Record<string, string> = { q: '', service: '', region: '', account: '' };
  for (const t of q.tokens) {
    if (!t.propertyKey) out.q = out.q || String(t.value);
    else if (t.operator === '=' && TOKEN_PARAMS[t.propertyKey]) out[TOKEN_PARAMS[t.propertyKey]] = out[TOKEN_PARAMS[t.propertyKey]] || String(t.value);
  }
  return out;
};
const haystack = (r: DeprecationItem) =>
  `${r.service_name} ${serviceLabel(r.service_name)} ${itemName(r)} ${JSON.stringify(r.service_specific)} ${r.region || ''} ${r.account_id || ''} ${r.account_name || ''}`.toLowerCase();
const matchToken = (r: DeprecationItem, t: PropertyFilterToken): boolean => {
  const v = String(t.value ?? '').toLowerCase();
  if (!t.propertyKey) return t.operator === '!:' ? !haystack(r).includes(v) : haystack(r).includes(v);
  const field = String((r as any)[t.propertyKey] ?? '').toLowerCase();
  return t.operator === '!=' ? field !== v : field === v;
};
const matchQuery = (r: DeprecationItem, q: PropertyFilterQuery): boolean =>
  q.tokens.length === 0 || (q.operation === 'or' ? q.tokens.some((t) => matchToken(r, t)) : q.tokens.every((t) => matchToken(r, t)));

const FILTER_I18N: PropertyFilterProps.I18nStrings = {
  filteringAriaLabel: 'Filter resources',
  filteringPlaceholder: 'Search versions and resources, or filter by service, region, account',
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

const PRIORITY_OPTIONS = [
  { label: 'Low', value: 'low' }, { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' }, { label: 'Critical', value: 'critical' },
];

const relative = (iso: string | null | undefined): string => {
  if (!iso) return 'never';
  const h = Math.round((Date.now() - new Date(iso).getTime()) / 3_600_000);
  return h < 1 ? 'less than an hour ago' : h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
};

export default function MyResources() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<DeprecationItem[]>([]);
  const [facts, setFacts] = useState<DeprecationItem[]>([]);
  const [plans, setPlans] = useState<ActionPlan[]>([]);
  const [coverage, setCoverage] = useState<ScanCoverage | null>(null);
  const [loading, setLoading] = useState(true);
  const [flashbarItems, setFlashbarItems] = useState<FlashbarProps.MessageDefinition[]>([]);
  const [scope, setScope] = useState(params.get('status') || 'concerns');
  const [preferences, setPreferences] = useState(loadPrefs);
  const [selected, setSelected] = useState<DeprecationItem[]>([]);
  const [showPlanModal, setShowPlanModal] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({ owner: '', priority: 'medium', target_date: '', notes: '' });
  // Row whose resource list is open in the details view (?details=<item_id>)
  const detailsId = params.get('details');

  useEffect(() => { load(); }, []);

  const flash = (type: FlashbarProps.Type, content: string) =>
    setFlashbarItems([{ type, content, dismissible: true, dismissLabel: 'Dismiss', onDismiss: () => setFlashbarItems([]), id: `${type}-${Date.now()}` }]);

  const load = async () => {
    try {
      setLoading(true);
      const [data, p, cov] = await Promise.all([getLifecycleData(), getActionPlans(), getScanners()]);
      setRows(data.inventory);
      setFacts(data.facts);
      setPlans(p);
      setCoverage(cov);
    } catch (err: any) {
      flash('error', `Failed to load resources: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  // plan owner per inventory row (item_id match)
  const planByItem = useMemo(() => {
    const m = new Map<string, ActionPlan>();
    for (const p of plans) m.set(`${p.service_name}|${p.item_id}`, p);
    return m;
  }, [plans]);

  const factById = useMemo(() => {
    const m = new Map<string, DeprecationItem>();
    for (const f of facts) m.set(`${f.service_name}|${f.item_id}`, f);
    return m;
  }, [facts]);

  const services = useMemo(() => [...new Set(rows.map((r) => r.service_name))].sort(), [rows]);
  // Accounts seen in the inventory (#144); the column and filter only appear with more than one
  const accounts = useMemo(() => accountsIn(rows), [rows]);
  const multiAccount = accounts.length > 1;

  // Scope (dropdown) narrows the rows and sets their default order; the
  // property filter, column sorting and pagination run on top via useCollection.
  const scoped = useMemo(() => {
    let out = [...rows];
    if (scope === 'concerns') out = out.filter((r) => isConcern(r.status));
    else if (scope === 'cost') out = out.filter((r) => (costExposure(r)?.resources_priced ?? 0) > 0);
    else if (isBucket(scope)) out = out.filter((r) => exposureBucket(r) === scope);
    else if (scope !== 'all') out = out.filter((r) => r.status === scope);
    out.sort(urgencySort);
    if (scope === 'cost') {
      // money first: billing now, then soonest start, then amount
      out.sort((a, b) => (costExposure(b)?.forecast_12m ?? 0) - (costExposure(a)?.forecast_12m ?? 0)
        || (costExposure(b)?.monthly ?? 0) - (costExposure(a)?.monthly ?? 0));
    }
    return out;
  }, [rows, scope]);

  const filteringProperties: PropertyFilterProps.FilteringProperty[] = useMemo(() => [
    { key: 'service_name', propertyLabel: 'Service', groupValuesLabel: 'Services', operators: ['=', '!='] },
    { key: 'region', propertyLabel: 'Region', groupValuesLabel: 'Regions', operators: ['=', '!='] },
    ...(multiAccount ? [{ key: 'account_id', propertyLabel: 'Account', groupValuesLabel: 'Accounts', operators: ['=', '!='] } as PropertyFilterProps.FilteringProperty] : []),
  ], [multiAccount]);
  const filteringOptions: PropertyFilterProps.FilteringOption[] = useMemo(() => [
    ...services.map((s) => ({ propertyKey: 'service_name', value: s, label: serviceLabel(s) })),
    ...[...new Set(rows.map((r) => r.region).filter(Boolean))].sort().map((v) => ({ propertyKey: 'region', value: v! })),
    ...(multiAccount ? accounts.map((a) => ({ propertyKey: 'account_id', value: a.id, label: accountLabel(a.id, a.name) })) : []),
  ], [rows, services, accounts, multiAccount]);

  const { items, allPageItems, filteredItemsCount, collectionProps, propertyFilterProps, paginationProps } = useCollection(scoped, {
    propertyFiltering: { filteringProperties, defaultQuery: queryFromParams(params), filteringFunction: matchQuery },
    sorting: {},
    pagination: { pageSize: preferences.pageSize },
  });

  // Functional update: the panel's onClose may run long after this render
  const updateParams = (next: Record<string, string>) => setParams((prev) => {
    const p = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(next)) { if (v && v !== 'all' && v !== 'concerns') p.set(k, v); else p.delete(k); }
    return p;
  }, { replace: true });

  const detailsRow = useMemo(() => (detailsId ? rows.find((r) => r.item_id === detailsId) || null : null), [rows, detailsId]);
  // Same arithmetic as the dashboard KPI, over the rows shown, so the two reconcile
  const costTotals = useMemo(() => (scope === 'cost' ? costTimeline([...allPageItems]) : null), [scope, allPageItems]);
  const shown = filteredItemsCount ?? scoped.length;
  const hasTokens = propertyFilterProps.query.tokens.length > 0;
  const clearFilters = () => {
    propertyFilterProps.onChange({ detail: { tokens: [], operation: 'and' } } as any);
    setScope('all');
    updateParams({ q: '', service: '', region: '', account: '', status: 'all' });
  };
  const factFor = (r: DeprecationItem) =>
    r.service_specific?.matched_lifecycle_item ? factById.get(`${r.service_name}|${r.service_specific.matched_lifecycle_item}`) : undefined;

  // The details of the ?details= row live in the AppLayout split panel
  const setPanel = useSplitPanel();
  useEffect(() => {
    if (!detailsRow) { setPanel(null); return; }
    setPanel({
      header: resourceDetailsHeader(detailsRow),
      content: <ResourceDetails row={detailsRow} fact={factFor(detailsRow)} plan={planByItem.get(`${detailsRow.service_name}|${detailsRow.item_id}`)} multiAccount={multiAccount} />,
      onClose: () => updateParams({ details: '' }),
    });
  }, [detailsRow, factById, planByItem, multiAccount]);
  useEffect(() => () => setPanel(null), []);

  const handleAddToPlan = async () => {
    if (!form.owner.trim()) { flash('error', 'Owner is required'); return; }
    setSubmitting(true);
    let ok = 0, ko = 0;
    for (const r of selected) {
      try {
        const res = await createActionPlan({
          service_name: r.service_name, item_id: r.item_id,
          item_name: `${serviceLabel(r.service_name)} ${itemName(r)} (${resourceCount(r)} ${resourceWord(resourceCount(r))})`,
          owner: form.owner, priority: form.priority, target_date: form.target_date, notes: form.notes,
        });
        res.success ? ok++ : ko++;
      } catch { ko++; }
    }
    setSubmitting(false);
    setShowPlanModal(false);
    setForm({ owner: '', priority: 'medium', target_date: '', notes: '' });
    setSelected([]);
    flash(ko ? 'warning' : 'success', ko ? `Created ${ok} plan(s), ${ko} failed` : `Created ${ok} plan${ok === 1 ? '' : 's'}`);
    load();
  };

  return (
    <SpaceBetween size="l">
      <Flashbar items={flashbarItems} stackItems />

      <Table
        {...collectionProps}
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        trackBy="item_id"
        items={items}
        columnDisplay={preferences.contentDisplay}
        pagination={<Pagination {...paginationProps} />}
        preferences={
          <CollectionPreferences
            title="Preferences" confirmLabel="Confirm" cancelLabel="Cancel"
            preferences={preferences}
            onConfirm={({ detail }) => { setPreferences(detail); localStorage.setItem(PREFS_KEY, JSON.stringify(detail)); }}
            pageSizePreference={{ title: 'Page size', options: [25, 50, 100].map((n) => ({ value: n, label: `${n} versions` })) }}
            contentDisplayPreference={{
              title: 'Columns',
              options: [
                { id: 'service', label: 'Service', alwaysVisible: true }, { id: 'version', label: 'Version', alwaysVisible: true },
                { id: 'status', label: 'Status' }, { id: 'deadline', label: 'Deadline' }, { id: 'resources', label: 'Resources' },
                { id: 'cost', label: 'Cost exposure' }, { id: 'account', label: 'Account' }, { id: 'region', label: 'Region' },
                { id: 'plan', label: 'Plan' }, { id: 'verified', label: 'Seen' },
              ],
            }}
          />
        }
        loading={loading}
        loadingText="Loading your resources..."
        variant="full-page"
        stickyHeader
        header={
          <Header
            variant="h1"
            info={<InfoLink />}
            counter={shown === scoped.length ? `(${shown})` : `(${shown} of ${scoped.length})`}
            description={costTotals
              ? `RDS/Aurora Extended Support: ${formatUsd(costTotals.forecast12)} over the next 12 months across ${costTotals.priced} priced resource${costTotals.priced === 1 ? '' : 's'}${costTotals.now ? `, ${costTotals.now} billing now (${formatUsd(costTotals.monthlyNow)}/mo)` : ''}${costTotals.within12 ? `, ${costTotals.within12} starting within 12 months (+${formatUsd(costTotals.monthlyWithin12)}/mo)` : ''}${costTotals.later ? `, ${costTotals.later} later` : ''}. Estimates assume always-on at current size.`
              : `Every runtime, engine or platform version the AWS account resource scan found, matched against the catalog of retiring versions; open a row for the resources behind it. Last scan ${relative(coverage?.last_scan.last_verified)}${(coverage?.last_scan.accounts.length ?? 0) > 1 ? ` across ${coverage!.last_scan.accounts.length} accounts` : ''}${coverage?.last_scan.regions.length ? ` in ${coverage.last_scan.regions.join(', ')}` : ''}.`}
            actions={
              <Button variant="primary" disabled={selected.length === 0} onClick={() => setShowPlanModal(true)}>
                Create plan{selected.length ? ` (${selected.length})` : ''}
              </Button>
            }
          >
            {scope === 'all' ? 'All AWS versions running in your resources'
              : scope === 'cost' ? 'RDS Extended Support exposure'
              : isBucket(scope) ? `${EXPOSURE_BUCKETS[scope]}: versions running in your resources`
              : scope === 'supported' ? 'Supported AWS versions running in your resources'
              : 'Deprecated AWS versions impacting your resources'}
          </Header>
        }
        filter={
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'flex-start' }}>
            <Select selectedOption={FLAT_SCOPE_OPTIONS.find((o) => o.value === scope) || FLAT_SCOPE_OPTIONS[0]}
              onChange={({ detail }) => { setScope(detail.selectedOption.value!); updateParams({ status: detail.selectedOption.value! }); }}
              options={SCOPE_OPTIONS} selectedAriaLabel="Selected" />
            <div style={{ flex: '1 1 480px' }}>
              <PropertyFilter
                {...propertyFilterProps}
                filteringOptions={filteringOptions}
                onChange={(e) => { propertyFilterProps.onChange(e); updateParams(paramsFromQuery(e.detail)); }}
                i18nStrings={FILTER_I18N}
                countText={`${shown} match${shown === 1 ? '' : 'es'}`}
                expandToViewport
              />
            </div>
          </div>
        }
        empty={
          <Box textAlign="center" padding="l" color="text-body-secondary">
            <SpaceBetween size="xs">
              <Box variant="strong">
                {rows.length === 0 ? 'No resources scanned yet' : scope === 'concerns' && !hasTokens ? 'Nothing needs attention' : 'No resources match these filters'}
              </Box>
              <Box variant="p">
                {rows.length === 0
                  ? 'Choose Refresh on My exposure to scan your accounts.'
                  : `Last scan ${relative(coverage?.last_scan.last_verified)}.${scope === 'concerns' && !hasTokens ? ' Supported and unmatched versions are under Everything found.' : ''}`}
              </Box>
              {rows.length === 0
                ? <Button onClick={() => navigate('/dashboard')}>Go to My exposure</Button>
                : scope === 'concerns' && !hasTokens
                  ? <Button onClick={() => { setScope('all'); updateParams({ status: 'all' }); }}>Show everything found</Button>
                  : <Button onClick={clearFilters}>Clear filters</Button>}
            </SpaceBetween>
          </Box>
        }
        columnDefinitions={[
          { id: 'service', header: 'Service', cell: (r) => <Badge color="blue">{serviceLabel(r.service_name)}</Badge>, sortingField: 'service_name' },
          {
            id: 'version', header: 'Version', cell: (r) => (
              <SpaceBetween size="xxxs">
                <Box variant="strong">{itemName(r)}</Box>
                <Box variant="small" color="text-body-secondary">{r.service_specific?.identifier}</Box>
              </SpaceBetween>
            ),
          },
          {
            id: 'status', header: 'Status', sortingComparator: (a, b) => statusMeta(a.status).rank - statusMeta(b.status).rank, cell: (r) => {
              const m = statusMeta(r.status);
              const fact = factFor(r);
              return (
                <SpaceBetween size="xxxs">
                  <StatusIndicator type={m.indicator}>{m.label}</StatusIndicator>
                  {fact ? (
                    <Popover dismissButton={false} position="right" size="medium" triggerType="text"
                      content={
                        <SpaceBetween size="xxs">
                          <Box variant="strong">Matched catalog entry</Box>
                          <Box variant="small">{itemName(fact)}</Box>
                          {Object.entries(fact.service_specific).filter(([k, v]) => k.endsWith('_date') && v && v !== 'N/A').map(([k, v]) => (
                            <Box key={k} variant="small">{k.replace(/_/g, ' ')}: {String(v)}</Box>
                          ))}
                          {fact.source_url && <Link href={fact.source_url} external fontSize="body-s">AWS documentation</Link>}
                        </SpaceBetween>
                      }>
                      <Box variant="small" color="text-status-info">why?</Box>
                    </Popover>
                  ) : r.status === 'unknown' ? <Box variant="small" color="text-body-secondary">no catalog entry</Box> : null}
                </SpaceBetween>
              );
            },
          },
          {
            id: 'deadline', header: 'Deadline',
            sortingComparator: (a, b) => (getDeadline(a)?.daysLeft ?? Number.MAX_SAFE_INTEGER) - (getDeadline(b)?.daysLeft ?? Number.MAX_SAFE_INTEGER),
            cell: (r) => {
              const d = getDeadline(r);
              return d ? (
                <SpaceBetween size="xxxs">
                  <Box>{formatDate(d.date)}</Box>
                  <Box variant="small" color={d.daysLeft < 0 ? 'text-status-error' : d.daysLeft <= 90 ? 'text-status-warning' : 'text-body-secondary'}>{formatDaysLeft(d)}</Box>
                </SpaceBetween>
              ) : <Box color="text-body-secondary">-</Box>;
            },
          },
          {
            id: 'resources', header: 'Resources', cell: (r) => {
              const n = resourceCount(r);
              const h = healthFlagged(r);
              return n
                ? <SpaceBetween size="xxxs">
                    <Link onFollow={(e) => { e.preventDefault(); updateParams({ details: r.item_id }); }} href="#" ariaLabel={`Show the ${n} ${resourceWord(n)} of ${itemName(r)}`}>
                      {n} {resourceWord(n)}
                    </Link>
                    {h > 0 && <Box variant="small" color="text-status-warning">{h} flagged by AWS Health</Box>}
                  </SpaceBetween>
                : <Box color="text-body-secondary">0</Box>;
            },
          },
          {
            id: 'cost', header: 'Cost exposure', sortingComparator: (a, b) => (costExposure(a)?.forecast_12m ?? 0) - (costExposure(b)?.forecast_12m ?? 0),
            cell: (r) => {
              const c = costExposure(r);
              if (!c || !c.resources_priced) {
                return (r.service_name === 'rds' || r.service_name === 'aurora')
                  ? <Box variant="small" color="text-body-secondary">{c ? 'no Extended Support' : '-'}</Box>
                  : <Box color="text-body-secondary">-</Box>;
              }
              return (
                <SpaceBetween size="xxxs">
                  <Link onFollow={(e) => { e.preventDefault(); updateParams({ details: r.item_id }); }} href="#" ariaLabel={`Extended Support estimate for ${itemName(r)}`}>
                    <Box variant="strong" color={c.in_extended_support ? 'text-status-error' : 'inherit'}>{formatUsd(c.monthly)}/mo</Box>
                  </Link>
                  <Box variant="small" color="text-body-secondary">
                    {c.in_extended_support
                      ? 'billing now'
                      : c.forecast_12m > 0
                        ? `${formatUsd(c.forecast_12m)} next 12 mo${(() => { const t = costTimeline([r]); return t.nextStart ? ` (from ${formatMonth(t.nextStart)})` : ''; })()}`
                        : `from ${formatMonth(costTimeline([r]).nextStart) || 'a later date'}`}{c.estimated ? ' · est.' : ''}
                  </Box>
                </SpaceBetween>
              );
            },
          },
          ...(multiAccount ? [{
            id: 'account', header: 'Account', sortingField: 'account_id',
            cell: (r: DeprecationItem) => r.account_id
              ? <SpaceBetween size="xxxs">
                  <Box>{r.account_name || r.account_id}</Box>
                  {r.account_name && <Box variant="small" color="text-body-secondary">{r.account_id}</Box>}
                </SpaceBetween>
              : <Box color="text-body-secondary">-</Box>,
          } as TableProps.ColumnDefinition<DeprecationItem>] : []),
          { id: 'region', header: 'Region', cell: (r) => r.region || '-', sortingField: 'region' },
          {
            id: 'plan', header: 'Plan', cell: (r) => {
              const p = planByItem.get(`${r.service_name}|${r.item_id}`);
              return p
                ? <StatusIndicator type={p.plan_status === 'completed' ? 'success' : p.plan_status === 'blocked' ? 'error' : p.plan_status === 'in_progress' ? 'in-progress' : 'pending'}>{p.owner}</StatusIndicator>
                : isConcern(r.status) ? <Box variant="small" color="text-body-secondary">unassigned</Box> : null;
            },
          },
          { id: 'verified', header: 'Seen', cell: (r) => <Box variant="small">{formatDate(r.last_verified)}</Box> },
        ]}
      />

      <Modal
        visible={showPlanModal}
        onDismiss={() => setShowPlanModal(false)}
        header={`Create plan for ${selected.length} version${selected.length === 1 ? '' : 's'}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowPlanModal(false)}>Cancel</Button>
              <Button variant="primary" loading={submitting} onClick={handleAddToPlan}>Create</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box variant="small" color="text-body-secondary">
            {selected.map((r) => `${serviceLabel(r.service_name)} ${itemName(r)}`).join(' · ')}
          </Box>
          <FormField label="Owner (email/alias)">
            <Input value={form.owner} onChange={({ detail }) => setForm({ ...form, owner: detail.value })} placeholder="e.g. jane@example.com" />
          </FormField>
          <FormField label="Priority">
            <Select selectedOption={PRIORITY_OPTIONS.find((o) => o.value === form.priority) || null}
              onChange={({ detail }) => setForm({ ...form, priority: detail.selectedOption.value || 'medium' })} options={PRIORITY_OPTIONS} />
          </FormField>
          <FormField label="Target date">
            <DatePicker value={form.target_date} onChange={({ detail }) => setForm({ ...form, target_date: detail.value })} placeholder="YYYY/MM/DD" />
          </FormField>
          <FormField label="Notes">
            <Textarea value={form.notes} onChange={({ detail }) => setForm({ ...form, notes: detail.value })} placeholder="Migration plan, blockers, ..." />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
}
