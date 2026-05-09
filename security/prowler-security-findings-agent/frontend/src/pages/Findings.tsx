import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  ButtonDropdown,
  CollectionPreferences,
  ContentLayout,
  ExpandableSection,
  Header,
  PropertyFilter,
  PropertyFilterProps,
  Table,
  SpaceBetween,
  StatusIndicator,
  Toggle,
} from '@cloudscape-design/components';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Finding, generateInsights, investigateFinding, listFindings } from '../api';
import { SEVERITY_ORDER } from '../theme';
import { FRAMEWORKS, frameworkLabelsForFinding, getFrameworkByKey, matchesFramework } from '../frameworks';
import { badgeFromHistory } from '../status-history';

function statusChip(s: string) {
  if (s === 'FAIL') return <StatusIndicator type="error">FAIL</StatusIndicator>;
  if (s === 'PASS') return <StatusIndicator type="success">PASS</StatusIndicator>;
  if (s === 'MANUAL') return <StatusIndicator type="warning">MANUAL</StatusIndicator>;
  return <StatusIndicator type="info">{s}</StatusIndicator>;
}

interface FilterToken {
  propertyKey: string;
  operator: '=' | '!=' | ':';
  value: string;
}

const COLUMN_DEFINITIONS = [
  {
    id: 'severity',
    header: 'Severity',
    cell: (it: Finding) => {
      // Group rows don't have history meaningful to render here; pass through.
      const badge = typeof (it as any).__group !== 'undefined' ? { kind: 'stable' as const } : badgeFromHistory(it);
      return (
        <>
          <span className={`soc-severity-chip soc-severity-chip--${it.severity}`}>{it.severity}</span>
          {badge.kind !== 'stable' && (
            <span
              className={`soc-history-chip soc-history-chip--${badge.kind}`}
              title={
                badge.kind === 'fixed' ? `Status flipped to PASS in scan ${badge.since}`
                : badge.kind === 'regressed' ? `Was PASS, now ${badge.wasStatus}`
                : 'First observed in the latest scan'
              }
            >
              {badge.label}
            </span>
          )}
        </>
      );
    },
    sortingField: 'severity',
    sortingComparator: (a: Finding, b: Finding) => (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99),
    width: 120,
  },
  {
    id: 'status',
    header: 'Status',
    cell: (it: Finding) => statusChip(it.status),
    sortingField: 'status',
    sortingComparator: (a: Finding, b: Finding) => (a.status || '').localeCompare(b.status || ''),
    width: 120,
  },
  {
    id: 'title',
    header: 'Check',
    cell: (it: Finding) => (
      <Box>
        <Box fontWeight="bold">{it.check_title || it.check_id}</Box>
        <Box variant="small" color="text-status-inactive">{it.check_id}</Box>
      </Box>
    ),
    sortingField: 'title',
    sortingComparator: (a: Finding, b: Finding) => (a.check_title || a.check_id || '').localeCompare(b.check_title || b.check_id || ''),
  },
  {
    id: 'service',
    header: 'Service',
    cell: (it: Finding) => it.service_name,
    sortingField: 'service',
    sortingComparator: (a: Finding, b: Finding) => (a.service_name || '').localeCompare(b.service_name || ''),
    width: 140,
  },
  {
    id: 'resource',
    header: 'Resource',
    cell: (it: Finding) => {
      // In group-by mode rows carry "<n> resource(s)"; render without code styling + italic.
      if (typeof (it as any).__group !== 'undefined') {
        const fail = (it as any).__failCount || 0;
        return (
          <Box variant="small">
            <span style={{ fontStyle: 'italic' }}>{it.resource_uid}</span>
            {fail > 0 && (
              <span style={{ marginLeft: 8, color: 'var(--soc-critical)', fontWeight: 600 }}>
                {fail} FAIL
              </span>
            )}
          </Box>
        );
      }
      return (
        <Box variant="code">
          <span translate="no" title={it.resource_uid}>
            {it.resource_uid.length > 60 ? it.resource_uid.slice(0, 57) + '…' : it.resource_uid}
          </span>
        </Box>
      );
    },
    sortingField: 'resource',
    sortingComparator: (a: Finding, b: Finding) => (a.resource_uid || '').localeCompare(b.resource_uid || ''),
  },
  {
    id: 'region',
    header: 'Region',
    cell: (it: Finding) => it.region || '—',
    sortingField: 'region',
    sortingComparator: (a: Finding, b: Finding) => (a.region || '').localeCompare(b.region || ''),
    width: 120,
  },
  {
    id: 'account',
    header: 'Account',
    cell: (it: Finding) => <Box variant="code"><span translate="no">{it.account_id || '—'}</span></Box>,
    sortingField: 'account',
    sortingComparator: (a: Finding, b: Finding) => (a.account_id || '').localeCompare(b.account_id || ''),
    width: 140,
  },
  {
    id: 'compliance',
    header: 'Compliance',
    cell: (it: Finding) => {
      const labels = frameworkLabelsForFinding(it);
      if (!labels.length) return <Box color="text-status-inactive" variant="small">—</Box>;
      const shown = labels.slice(0, 3).join(' · ');
      return <Box variant="small">{shown}{labels.length > 3 ? ` +${labels.length - 3}` : ''}</Box>;
    },
    sortingField: 'compliance',
    sortingComparator: (a: Finding, b: Finding) => frameworkLabelsForFinding(a).length - frameworkLabelsForFinding(b).length,
    width: 220,
  },
  {
    id: 'scan',
    header: 'Scan',
    cell: (it: Finding) => <Box variant="small"><span translate="no">{it.scan_id || '—'}</span></Box>,
    sortingField: 'scan',
    sortingComparator: (a: Finding, b: Finding) => (a.scan_id || '').localeCompare(b.scan_id || ''),
    width: 170,
  },
  {
    id: 'insights',
    header: 'Insights',
    cell: (it: Finding) => (it.remediation_s3_key ? <StatusIndicator type="success">Ready</StatusIndicator> : <Box color="text-status-inactive" variant="small">—</Box>),
    sortingField: 'insights',
    sortingComparator: (a: Finding, b: Finding) => Number(Boolean(b.remediation_s3_key)) - Number(Boolean(a.remediation_s3_key)),
    width: 110,
  },
  {
    id: 'lastSeen',
    header: 'Last seen',
    cell: (it: Finding) => (it.last_seen_at ? new Date(it.last_seen_at).toLocaleString() : '—'),
    sortingField: 'lastSeen',
    sortingComparator: (a: Finding, b: Finding) => (a.last_seen_at || '').localeCompare(b.last_seen_at || ''),
    width: 180,
  },
] as const;

const DEFAULT_VISIBLE_COLUMNS = ['severity', 'status', 'title', 'service', 'resource', 'region', 'compliance', 'insights', 'lastSeen'];

// Map PropertyFilter token propertyKey → URL query-string key.
// Only "canonical" filters (those we can deep-link to) survive the round-trip.
const URL_FILTER_KEYS: Record<string, string> = {
  severity: 'severity',
  status: 'status',
  scan_id: 'scan',
  service_name: 'service',
  check_id: 'check',
};

function tokensToParams(tokens: FilterToken[], framework: string | null, groupByCheck: boolean): URLSearchParams {
  const p = new URLSearchParams();
  for (const t of tokens) {
    const urlKey = URL_FILTER_KEYS[t.propertyKey];
    if (urlKey && t.operator === '=' && t.value) p.set(urlKey, t.value);
  }
  if (framework) p.set('framework', framework);
  if (groupByCheck) p.set('groupBy', 'check');
  return p;
}

export default function Findings() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [items, setItems] = useState<Finding[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState<{ tokens: FilterToken[]; operation: 'and' | 'or' }>({ tokens: [], operation: 'and' });
  // Cloudscape Table's controlled sort needs the *column definition object*
  // (not just a sortingField string) or header clicks won't re-sort.
  const [sortingColumn, setSortingColumn] = useState<typeof COLUMN_DEFINITIONS[number]>(COLUMN_DEFINITIONS[0]);
  const [sortingDescending, setSortingDescending] = useState<boolean>(false);
  const [visibleColumns, setVisibleColumns] = useState<string[]>(DEFAULT_VISIBLE_COLUMNS);
  // Framework filter is a first-class filter (not a PropertyFilter token) because
  // it matches a regex across compliance_frameworks / check_id / check_title,
  // which PropertyFilter's simple string compare can't express.
  const [frameworkFilter, setFrameworkFilter] = useState<string | null>(null);
  const [groupByCheck, setGroupByCheck] = useState<boolean>(false);
  const [selected, setSelected] = useState<Finding[]>([]);
  const [bulkBusy, setBulkBusy] = useState<null | 'investigate' | 'insights'>(null);
  const [bulkMessage, setBulkMessage] = useState<string | null>(null);

  // URL → state (once, on first mount / deep-link navigations)
  useEffect(() => {
    const tokens: any[] = [];
    const severity = searchParams.get('severity');
    const status = searchParams.get('status');
    const scanId = searchParams.get('scan');
    const service = searchParams.get('service');
    const check = searchParams.get('check');
    const framework = searchParams.get('framework');
    const groupBy = searchParams.get('groupBy');
    if (severity) tokens.push({ propertyKey: 'severity', operator: '=', value: severity });
    if (status) tokens.push({ propertyKey: 'status', operator: '=', value: status });
    if (scanId) tokens.push({ propertyKey: 'scan_id', operator: '=', value: scanId });
    if (service) tokens.push({ propertyKey: 'service_name', operator: '=', value: service });
    if (check) tokens.push({ propertyKey: 'check_id', operator: '=', value: check });
    if (tokens.length) setQuery({ tokens, operation: 'and' });
    if (framework) setFrameworkFilter(framework);
    if (groupBy === 'check') setGroupByCheck(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // state → URL (keeps the URL shareable / back-button friendly).
  useEffect(() => {
    const next = tokensToParams(query.tokens, frameworkFilter, groupByCheck);
    setSearchParams(next, { replace: true });
  }, [query, frameworkFilter, groupByCheck, setSearchParams]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const res = await listFindings({ limit: 500 });
      setItems(res.items || []);
    } catch (e: any) {
      setError(e?.message || 'Failed to load findings');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  const filtered = useMemo(() => {
    const fw = frameworkFilter ? getFrameworkByKey(frameworkFilter) : undefined;
    return items.filter((item) => {
      if (fw && !matchesFramework(item, fw.match)) return false;
      return query.tokens.every((t) => {
        const val = String((item as Record<string, unknown>)[t.propertyKey] ?? '').toLowerCase();
        const needle = String(t.value).toLowerCase();
        if (t.operator === '=') return val === needle;
        if (t.operator === '!=') return val !== needle;
        return val.includes(needle);
      });
    });
  }, [items, query, frameworkFilter]);

  const sortedItems = useMemo(() => {
    const cmp = sortingColumn.sortingComparator;
    if (!cmp) return filtered;
    const arr = [...filtered].sort(cmp);
    return sortingDescending ? arr.reverse() : arr;
  }, [filtered, sortingColumn, sortingDescending]);

  // Group rows collapse multiple resources with the same check_id into one
  // row. Worst-severity wins; status is the "strictest" (FAIL > MANUAL > PASS).
  type GroupRow = Finding & { __group: true; __count: number; __failCount: number };
  const isGroupRow = (r: Finding | GroupRow): r is GroupRow => (r as GroupRow).__group === true;
  const groupedItems = useMemo<(Finding | GroupRow)[]>(() => {
    if (!groupByCheck) return sortedItems;
    const byCheck = new Map<string, Finding[]>();
    for (const f of sortedItems) {
      const arr = byCheck.get(f.check_id) || [];
      arr.push(f);
      byCheck.set(f.check_id, arr);
    }
    const rows: GroupRow[] = [];
    for (const [check_id, group] of byCheck) {
      // Pick a representative row + compute aggregates.
      const sorted = [...group].sort(
        (a, b) => (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99),
      );
      const head = sorted[0];
      const failCount = group.filter((g) => g.status === 'FAIL').length;
      const groupStatus = failCount > 0 ? 'FAIL' : (group.some((g) => g.status === 'MANUAL') ? 'MANUAL' : 'PASS');
      rows.push({
        ...head,
        // finding_uid must be unique across group rows — prefix the check_id so it cannot
        // collide with a real finding_uid (those are "check_id:resource_uid").
        finding_uid: `__group__:${check_id}`,
        status: groupStatus,
        resource_uid: `${group.length} resource${group.length === 1 ? '' : 's'}`,
        __group: true,
        __count: group.length,
        __failCount: failCount,
      });
    }
    return rows.sort(
      (a, b) => (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99),
    );
  }, [sortedItems, groupByCheck]);

  // Bulk handlers ---------------------------------------------------------

  /**
   * Fire a per-finding action with bounded concurrency so we don't torch
   * Bedrock/DevOps-Agent quotas for a 20-row selection.
   */
  async function runBulk(
    findings: Finding[],
    action: 'investigate' | 'insights',
    label: string,
  ) {
    if (!findings.length || bulkBusy) return;
    setBulkBusy(action);
    setBulkMessage(null);
    const CONCURRENCY = 3;
    let ok = 0;
    let fail = 0;
    const queue = [...findings];
    async function worker() {
      while (queue.length) {
        const f = queue.shift();
        if (!f) return;
        try {
          if (action === 'investigate') await investigateFinding(f.finding_uid);
          else await generateInsights(f.finding_uid);
          ok++;
        } catch {
          fail++;
        }
      }
    }
    await Promise.all(Array.from({ length: CONCURRENCY }, worker));
    setBulkMessage(
      `${label}: ${ok} succeeded${fail ? ` · ${fail} failed` : ''}. Refreshing findings…`,
    );
    setBulkBusy(null);
    setSelected([]);
    await load();
  }

  async function bulkInvestigate() {
    const items = selected.filter((s) => !isGroupRow(s));
    await runBulk(items, 'investigate', 'Investigations dispatched');
  }
  async function bulkInsights() {
    const items = selected.filter((s) => !isGroupRow(s) && !s.remediation_s3_key);
    await runBulk(items, 'insights', 'Bedrock Insights generated');
  }

  const activeFilterPills = query.tokens.map((t, i) => (
    <Button
      key={`${t.propertyKey}-${i}`}
      iconName="close"
      variant="inline-link"
      onClick={() => setQuery({
        tokens: query.tokens.filter((_, idx) => idx !== i),
        operation: query.operation,
      })}
    >
      {t.propertyKey} {t.operator} {t.value}
    </Button>
  ));

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="All Prowler findings for this account. Click a row to see the Bedrock insight and dispatch a DevOps Agent investigation."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Toggle checked={groupByCheck} onChange={(e) => setGroupByCheck(e.detail.checked)}>
                Group by check
              </Toggle>
              <Button iconName="refresh" onClick={load} loading={loading} ariaLabel="Refresh findings">Refresh</Button>
            </SpaceBetween>
          }
        >
          Findings
        </Header>
      }
    >
      <SpaceBetween size="l">
        {error && (
          <Alert type="error" dismissible onDismiss={() => setError(null)} action={<Button onClick={load}>Retry</Button>}>
            {error}
          </Alert>
        )}
        {bulkMessage && (
          <Alert type="success" dismissible onDismiss={() => setBulkMessage(null)}>{bulkMessage}</Alert>
        )}

        <ExpandableSection variant="footer" headerText="What do PASS, FAIL and MANUAL mean?">
          <SpaceBetween size="xs">
            <div><StatusIndicator type="error">FAIL</StatusIndicator> &nbsp;— Prowler evaluated the control and it did not pass. There is a concrete configuration to change. These are the findings that matter.</div>
            <div><StatusIndicator type="success">PASS</StatusIndicator> &nbsp;— Prowler evaluated the control and your account already complies. No action needed.</div>
            <div><StatusIndicator type="warning">MANUAL</StatusIndicator> &nbsp;— The check requires human judgement (policy context, data classification). Prowler lists it but cannot auto-decide.</div>
          </SpaceBetween>
        </ExpandableSection>

        {activeFilterPills.length > 0 && (
          <Box>
            <Box variant="awsui-key-label" margin={{ bottom: 'xs' }}>Active filters (click to remove)</Box>
            <SpaceBetween direction="horizontal" size="xs">{activeFilterPills}</SpaceBetween>
          </Box>
        )}

        <Table
          loading={loading}
          loadingText="Loading findings from DynamoDB…"
          items={groupedItems}
          trackBy="finding_uid"
          sortingColumn={sortingColumn}
          sortingDescending={sortingDescending}
          onSortingChange={(e) => {
            const id = e.detail.sortingColumn.sortingField;
            const match = COLUMN_DEFINITIONS.find((c) => c.sortingField === id);
            if (match) setSortingColumn(match);
            setSortingDescending(Boolean(e.detail.isDescending));
          }}
          visibleColumns={visibleColumns}
          preferences={
            <CollectionPreferences
              title="Preferences"
              confirmLabel="Confirm"
              cancelLabel="Cancel"
              preferences={{ visibleContent: visibleColumns }}
              visibleContentPreference={{
                title: 'Visible columns',
                options: [
                  {
                    label: 'Columns',
                    options: COLUMN_DEFINITIONS.map((c) => ({ id: c.id, label: String(c.header), editable: c.id !== 'severity' && c.id !== 'title' })),
                  },
                ],
              }}
              onConfirm={(e) => setVisibleColumns((e.detail.visibleContent as string[]) ?? DEFAULT_VISIBLE_COLUMNS)}
            />
          }
          selectionType={groupByCheck ? undefined : 'multi'}
          selectedItems={groupByCheck ? [] : selected}
          onSelectionChange={(e) => setSelected(e.detail.selectedItems)}
          onRowClick={(e) => {
            const row = e.detail.item;
            if (isGroupRow(row)) {
              // Drill down: switch off group-by and filter by this check
              setGroupByCheck(false);
              setQuery({
                operation: 'and',
                tokens: [{ propertyKey: 'check_id', operator: '=', value: row.check_id } as FilterToken],
              });
              setFrameworkFilter(null);
              return;
            }
            navigate(`/findings/${encodeURIComponent(row.finding_uid)}`);
          }}
          enableKeyboardNavigation
          header={
            <Header
              counter={groupByCheck
                ? `(${groupedItems.length} check${groupedItems.length === 1 ? '' : 's'} · ${filtered.length}/${items.length} finding${filtered.length === 1 ? '' : 's'})`
                : `(${filtered.length}/${items.length})`}
              description={groupByCheck
                ? 'Click a row to drill into the individual findings for that check.'
                : 'Click any row to open the detail view. Click any column header to sort.'}
              actions={!groupByCheck && (
                <SpaceBetween direction="horizontal" size="xs">
                  <Box color="text-status-inactive" variant="small">
                    {selected.length ? `${selected.length} selected` : 'Select rows for bulk actions'}
                  </Box>
                  <ButtonDropdown
                    disabled={selected.length === 0 || bulkBusy !== null}
                    loading={bulkBusy !== null}
                    items={[
                      {
                        id: 'investigate',
                        text: `Investigate ${selected.length} with DevOps Agent`,
                        disabled: selected.length === 0,
                      },
                      {
                        id: 'insights',
                        text: `Generate Bedrock Insights for ${selected.filter((s) => !s.remediation_s3_key).length}`,
                        disabled: selected.filter((s) => !s.remediation_s3_key).length === 0,
                      },
                    ]}
                    onItemClick={(e) => {
                      if (e.detail.id === 'investigate') bulkInvestigate();
                      else if (e.detail.id === 'insights') bulkInsights();
                    }}
                    variant="primary"
                  >
                    Bulk actions
                  </ButtonDropdown>
                </SpaceBetween>
              )}
            >
              {groupByCheck ? 'Findings grouped by check' : 'All findings'}
            </Header>
          }
          filter={
            <PropertyFilter
              query={{
                operation: query.operation,
                tokens: [
                  ...query.tokens,
                  ...(frameworkFilter ? [{ propertyKey: 'framework', operator: '=', value: frameworkFilter } as FilterToken] : []),
                ],
              } as unknown as PropertyFilterProps.Query}
              onChange={(e) => {
                const detail = e.detail as unknown as { tokens: FilterToken[]; operation: 'and' | 'or' };
                // Split the 'framework' pseudo-token out of the PropertyFilter query
                // because we resolve it with a regex, not a plain field compare.
                const fwToken = detail.tokens.find((t) => t.propertyKey === 'framework');
                setFrameworkFilter(fwToken ? fwToken.value : null);
                setQuery({
                  operation: detail.operation,
                  tokens: detail.tokens.filter((t) => t.propertyKey !== 'framework'),
                });
              }}
              filteringProperties={[
                { key: 'severity', operators: ['=', '!='], propertyLabel: 'Severity', groupValuesLabel: 'Severity values' },
                { key: 'status', operators: ['=', '!='], propertyLabel: 'Status', groupValuesLabel: 'Status values' },
                { key: 'service_name', operators: ['=', ':'], propertyLabel: 'Service', groupValuesLabel: 'Service values' },
                { key: 'check_id', operators: [':', '='], propertyLabel: 'Check ID', groupValuesLabel: 'Check IDs' },
                { key: 'resource_uid', operators: [':'], propertyLabel: 'Resource', groupValuesLabel: 'Resources' },
                { key: 'scan_id', operators: ['='], propertyLabel: 'Scan', groupValuesLabel: 'Scan IDs' },
                { key: 'framework', operators: ['='], propertyLabel: 'Compliance framework', groupValuesLabel: 'Frameworks' },
              ]}
              filteringOptions={FRAMEWORKS.map((fw) => ({ propertyKey: 'framework', value: fw.key, label: fw.label }))}
              i18nStrings={{
                filteringAriaLabel: 'Filter findings',
                dismissAriaLabel: 'Dismiss',
                filteringPlaceholder: 'Filter by severity, status, service, check, resource…',
                groupValuesText: 'Values',
                groupPropertiesText: 'Properties',
                operatorsText: 'Operators',
                operationAndText: 'and',
                operationOrText: 'or',
                operatorLessText: 'Less than',
                operatorLessOrEqualText: 'Less than or equal',
                operatorGreaterText: 'Greater than',
                operatorGreaterOrEqualText: 'Greater than or equal',
                operatorContainsText: 'Contains',
                operatorDoesNotContainText: 'Does not contain',
                operatorEqualsText: 'Equals',
                operatorDoesNotEqualText: 'Does not equal',
                applyActionText: 'Apply',
                clearFiltersText: 'Clear filters',
              }}
            />
          }
          columnDefinitions={COLUMN_DEFINITIONS as any}
          empty={
            <Box textAlign="center" padding={{ vertical: 'xl' }}>
              <SpaceBetween size="s">
                <Box variant="h3">No findings yet</Box>
                <Box color="text-status-inactive">
                  Trigger your first Prowler scan from the Dashboard. Typical scan takes 5–10 minutes.
                </Box>
                <Button variant="primary" onClick={() => navigate('/')}>Go to Dashboard</Button>
              </SpaceBetween>
            </Box>
          }
          stickyHeader
        />
      </SpaceBetween>
    </ContentLayout>
  );
}
