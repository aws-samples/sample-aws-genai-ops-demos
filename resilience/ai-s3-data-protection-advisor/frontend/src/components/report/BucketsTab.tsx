import { useEffect, useMemo, useRef, useState } from 'react';
import { useCollection } from '@cloudscape-design/collection-hooks';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import CollectionPreferences, { CollectionPreferencesProps } from '@cloudscape-design/components/collection-preferences';
import Container from '@cloudscape-design/components/container';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Pagination from '@cloudscape-design/components/pagination';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import TextFilter from '@cloudscape-design/components/text-filter';
import Toggle from '@cloudscape-design/components/toggle';
import { emptyFilter, filterRows, formatBytes, middleTruncate, options, RISK_REQ, summary } from '../../lib/buckets';
import { loadExclude, parsePatterns, saveExclude } from '../../lib/exclude';
import { riskName } from '../../lib/format';
import { bucketStatus, protectionStatus } from '../../lib/status';
import type { BucketRow, CardsTab, RiskKey } from '../../lib/types';
import BucketDetail from './BucketDetail';

const ANY = { label: 'Any', value: '' };
const RISKS: RiskKey[] = ['he', 'ma', 'rd'];
const SHORT: Record<RiskKey, string> = { he: 'Human error', ma: 'Malicious', rd: 'Regional' };
const ALL_COLUMNS = [
  { id: 'bucket', label: 'Bucket' }, { id: 'status', label: 'Status' }, { id: 'account', label: 'Account' },
  { id: 'region', label: 'Region' }, { id: 'role', label: 'Role' }, { id: 'he', label: 'Human error' },
  { id: 'ma', label: 'Malicious activity' }, { id: 'rd', label: 'Regional disruption' },
  { id: 'score', label: 'Priority' }, { id: 'action', label: 'Action' }, { id: 'size', label: 'Size' },
];

interface Props {
  tab: CardsTab;
  reportKey: string;
}

export default function BucketsTab({ tab, reportKey }: Props) {
  const all = useMemo(() => (tab.cards ?? []).map((c) => c.row).filter(Boolean), [tab]);
  const [f, setF] = useState(emptyFilter());
  const [excludeText, setExcludeText] = useState(() => loadExclude(reportKey));
  const [selected, setSelected] = useState<BucketRow | null>(null);

  const singleAccount = options(all, 'account').length <= 1;
  const [visibleContent, setVisibleContent] = useState<readonly string[]>(() =>
    ALL_COLUMNS.map((c) => c.id).filter((id) => !(singleAccount && (id === 'account' || id === 'action'))),
  );
  const detailRef = useRef<HTMLDivElement>(null);

  const patterns = useMemo(() => parsePatterns(excludeText), [excludeText]);
  const filter = { ...f, patterns };
  const visible = useMemo(() => filterRows(all, filter), [all, f, patterns]); // eslint-disable-line react-hooks/exhaustive-deps
  const stats = useMemo(() => summary(filterRows(all, { ...emptyFilter(), patterns })), [all, patterns]);

  const { items, collectionProps, paginationProps } = useCollection<BucketRow>(visible, {
    sorting: { defaultState: { sortingColumn: { sortingField: 'score' }, isDescending: true } },
    pagination: { pageSize: 50 },
  });

  // Drop the selection when its bucket leaves the visible set (filter or exclusion changed).
  useEffect(() => {
    if (selected && !visible.some((r) => r.bucket === selected.bucket)) setSelected(null);
  }, [visible, selected]);

  useEffect(() => {
    if (selected) detailRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
  }, [selected]);

  const opt = (key: 'account' | 'region' | 'role' | 'status' | 'tier') => [ANY, ...options(all, key).map((v) => ({ label: v, value: v }))];
  const sel = (key: 'account' | 'region' | 'role' | 'status' | 'tier', label: string) => (
    <FormField label={label}>
      <Select
        selectedOption={opt(key).find((o) => o.value === f[key]) ?? ANY}
        options={opt(key)}
        onChange={({ detail }) => setF({ ...f, [key]: detail.selectedOption.value ?? '' })}
      />
    </FormField>
  );

  return (
    <SpaceBetween size="m">
      <Container header={<Header variant="h2">Summary</Header>}>
        <ColumnLayout columns={4} variant="text-grid">
          <div><Box variant="awsui-key-label">Buckets</Box><Box variant="awsui-value-large">{stats.buckets}</Box></div>
          <div><Box variant="awsui-key-label">Total size</Box><Box variant="awsui-value-large">{formatBytes(stats.totalBytes)}</Box></div>
          <div><Box variant="awsui-key-label">Priority focus</Box><Box variant="awsui-value-large">{stats.spotlight}</Box></div>
          <div>
            <Box variant="awsui-key-label">Required but unprotected</Box>
            {RISKS.map((k) => (
              <Box key={k}>
                {riskName(k)}: <b>{stats.unprotectedRequired[k]}</b>
                {stats.unconfirmedRequired[k] > 0 && ` (${stats.unconfirmedRequired[k]} unconfirmed)`}
              </Box>
            ))}
          </div>
        </ColumnLayout>
      </Container>

      <Table
        {...collectionProps}
        variant="container"
        resizableColumns
        wrapLines={false}
        columnDisplay={ALL_COLUMNS.map((c) => ({ id: c.id, visible: visibleContent.includes(c.id) }))}
        preferences={
          <CollectionPreferences
            title="Preferences"
            confirmLabel="Confirm"
            cancelLabel="Cancel"
            preferences={{ visibleContent } as CollectionPreferencesProps.Preferences}
            onConfirm={({ detail }) => setVisibleContent(detail.visibleContent ?? [])}
            visibleContentPreference={{
              title: 'Columns',
              options: [{ label: 'Columns', options: ALL_COLUMNS.map((c) => ({ id: c.id, label: c.label, editable: c.id !== 'bucket' })) }],
            }}
          />
        }
        header={<Header variant="h2" counter={`(${visible.length} of ${all.length})`}>Buckets</Header>}
        items={items}
        trackBy="bucket"
        selectionType="single"
        selectedItems={selected ? [selected] : []}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems[0] ?? null)}
        ariaLabels={{ selectionGroupLabel: 'Select a bucket to see its detail', itemSelectionLabel: (_s, r) => r.bucket }}
        pagination={<Pagination {...paginationProps} />}
        filter={
          <SpaceBetween size="s">
            <TextFilter
              filteringText={f.text}
              filteringPlaceholder="Find buckets by name, account, region, role, action or reason"
              countText={`${visible.length} matches`}
              onChange={({ detail }) => setF({ ...f, text: detail.filteringText })}
            />
            <ColumnLayout columns={5}>
              {sel('account', 'Account')}
              {sel('region', 'Region')}
              {sel('role', 'Role')}
              {sel('status', 'Status')}
              {sel('tier', 'Priority')}
            </ColumnLayout>
            <ColumnLayout columns={2}>
              <Toggle checked={f.spotlightOnly} onChange={({ detail }) => setF({ ...f, spotlightOnly: detail.checked })}>Priority focus only</Toggle>
              <FormField label="Exclude buckets">
                <Input
                  value={excludeText}
                  placeholder="Comma-separated patterns, * as wildcard"
                  onChange={({ detail }) => { setExcludeText(detail.value); saveExclude(reportKey, detail.value); }}
                />
              </FormField>
            </ColumnLayout>
          </SpaceBetween>
        }
        empty="No buckets match the filters"
        columnDefinitions={[
          {
            id: 'bucket', header: 'Bucket', sortingField: 'bucket', width: 300, minWidth: 220,
            cell: (r) => <><span title={r.bucket}>{middleTruncate(r.bucket)}</span>{r.spotlight && <> <Badge color="red">Priority focus</Badge></>}</>,
          },
          { id: 'account', header: 'Account', sortingField: 'account', width: 130, minWidth: 90, cell: (r) => r.account },
          { id: 'region', header: 'Region', sortingField: 'region', width: 130, minWidth: 130, cell: (r) => r.region },
          { id: 'role', header: 'Role', sortingField: 'role', width: 140, minWidth: 140, cell: (r) => r.role },
          { id: 'size', header: 'Size', sortingField: 'size_bytes', width: 120, minWidth: 120, cell: (r) => r.size_human },
          ...RISKS.map((k) => ({
            id: k,
            header: SHORT[k],
            width: 140,
            minWidth: 130,
            cell: (r: BucketRow) => {
              const st = protectionStatus(r[k]);
              return (
                <SpaceBetween size="xxs">
                  <StatusIndicator type={st.type}>{st.label}</StatusIndicator>
                  {(r.req ?? []).includes(RISK_REQ[k]) && <Box variant="small" color="text-body-secondary">(required)</Box>}
                </SpaceBetween>
              );
            },
          })),
          { id: 'status', header: 'Status', sortingField: 'status', width: 200, minWidth: 200, cell: (r) => { const s = bucketStatus(r); return <div style={{ whiteSpace: 'normal' }}><StatusIndicator type={s.type} wrapText>{s.label}</StatusIndicator></div>; } },
          { id: 'score', header: 'Priority', sortingField: 'score', width: 100, minWidth: 80, cell: (r) => r.tier },
          { id: 'action', header: 'Action', width: 240, minWidth: 140, cell: (r) => r.action },
        ]}
      />

      {selected ? (
        <div ref={detailRef}><Container><BucketDetail row={selected} /></Container></div>
      ) : (
        <Box color="text-body-secondary">Select a bucket to see its controls, evidence and findings.</Box>
      )}
    </SpaceBetween>
  );
}

