import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CollectionPreferences,
  ContentLayout,
  ExpandableSection,
  Header,
  PropertyFilter,
  PropertyFilterProps,
  Table,
  SpaceBetween,
  StatusIndicator,
} from '@cloudscape-design/components';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Finding, listFindings } from '../api';
import { SEVERITY_ORDER } from '../theme';
import { FRAMEWORKS, frameworkLabelsForFinding, getFrameworkByKey, matchesFramework } from '../frameworks';

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
    cell: (it: Finding) => (
      <span className={`soc-severity-chip soc-severity-chip--${it.severity}`}>{it.severity}</span>
    ),
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
    cell: (it: Finding) => (
      <Box variant="code">
        <span translate="no" title={it.resource_uid}>
          {it.resource_uid.length > 60 ? it.resource_uid.slice(0, 57) + '…' : it.resource_uid}
        </span>
      </Box>
    ),
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

export default function Findings() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
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

  // Apply filters from URL on first load
  useEffect(() => {
    const tokens: any[] = [];
    const severity = searchParams.get('severity');
    const status = searchParams.get('status');
    const scanId = searchParams.get('scan');
    const service = searchParams.get('service');
    const framework = searchParams.get('framework');
    if (severity) tokens.push({ propertyKey: 'severity', operator: '=', value: severity });
    if (status) tokens.push({ propertyKey: 'status', operator: '=', value: status });
    if (scanId) tokens.push({ propertyKey: 'scan_id', operator: '=', value: scanId });
    if (service) tokens.push({ propertyKey: 'service_name', operator: '=', value: service });
    if (tokens.length) setQuery({ tokens, operation: 'and' });
    if (framework) setFrameworkFilter(framework);
  }, [searchParams]);

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
          actions={<Button iconName="refresh" onClick={load} loading={loading}>Refresh</Button>}
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
          items={sortedItems}
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
          onRowClick={(e) => navigate(`/findings/${encodeURIComponent(e.detail.item.finding_uid)}`)}
          header={<Header counter={`(${filtered.length}/${items.length})`} description="Click any row to open the detail view. Click any column header to sort.">All findings</Header>}
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
