// Details view for one inventory row: the only place the UI lists resource
// names (tables show counts only, see #141).
import { useMemo, useState } from 'react';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Link from '@cloudscape-design/components/link';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import TextFilter from '@cloudscape-design/components/text-filter';
import Header from '@cloudscape-design/components/header';
import type { DeprecationItem, ActionPlan } from '../api';
import {
  statusMeta, getDeadline, formatDate, formatDaysLeft, serviceLabel, itemName,
  resourceCount, resourceNames, resourceWord,
} from '../lifecycle';

interface Props {
  row: DeprecationItem | null;
  fact?: DeprecationItem;
  plan?: ActionPlan;
  onDismiss: () => void;
}

const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div>
    <Box variant="awsui-key-label">{label}</Box>
    <div>{children}</div>
  </div>
);

export default function ResourceDetails({ row, fact, plan, onDismiss }: Props) {
  const [filter, setFilter] = useState('');
  const names = useMemo(() => (row ? resourceNames(row) : []), [row]);
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? names.filter((n) => n.toLowerCase().includes(q)) : names;
  }, [names, filter]);

  if (!row) return null;
  const m = statusMeta(row.status);
  const d = getDeadline(row);
  const count = resourceCount(row);
  const dates = fact
    ? Object.entries(fact.service_specific || {}).filter(([k, v]) => k.endsWith('_date') && v && v !== 'N/A')
    : [];

  return (
    <Modal
      visible
      size="large"
      onDismiss={onDismiss}
      header={`${serviceLabel(row.service_name)} · ${itemName(row)}`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={() => navigator.clipboard?.writeText(shown.join('\n'))} disabled={!shown.length}>
              Copy names
            </Button>
            <Button variant="primary" onClick={onDismiss}>Close</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="l">
        <ColumnLayout columns={4} variant="text-grid">
          <Field label="Status"><StatusIndicator type={m.indicator}>{m.label}</StatusIndicator></Field>
          <Field label="Deadline">
            {d ? <>{formatDate(d.date)} <Box variant="small" color="text-body-secondary">{formatDaysLeft(d)}</Box></> : '-'}
          </Field>
          <Field label="Region">{row.region || '-'}</Field>
          <Field label="Plan">
            {plan ? <StatusIndicator type={plan.plan_status === 'completed' ? 'success' : 'in-progress'}>{plan.owner}</StatusIndicator> : <Box color="text-body-secondary">unassigned</Box>}
          </Field>
        </ColumnLayout>

        {fact ? (
          <Box>
            <Box variant="awsui-key-label">Matched catalog entry</Box>
            <SpaceBetween direction="horizontal" size="s">
              <Box>{itemName(fact)}</Box>
              {dates.map(([k, v]) => <Box key={k} variant="small">{k.replace(/_/g, ' ')}: {String(v)}</Box>)}
              {fact.source_url && <Link href={fact.source_url} external fontSize="body-s">AWS documentation</Link>}
            </SpaceBetween>
          </Box>
        ) : (
          <Box color="text-body-secondary">No catalog entry matched this version.</Box>
        )}

        <Table
          variant="embedded"
          items={shown.map((n, i) => ({ n, i }))}
          trackBy="n"
          header={
            <Header
              variant="h3"
              counter={`(${count})`}
              description={names.length < count ? `Showing the first ${names.length} names; the scan stored a capped list.` : undefined}
            >
              {resourceWord(count).replace(/^r/, 'R')}
            </Header>
          }
          filter={
            names.length > 10 ? (
              <TextFilter filteringText={filter} filteringPlaceholder="Find a resource" filteringAriaLabel="Filter resource names"
                countText={`${shown.length} match${shown.length === 1 ? '' : 'es'}`}
                onChange={({ detail }) => setFilter(detail.filteringText)} />
            ) : undefined
          }
          columnDefinitions={[
            { id: 'i', header: '#', cell: (x) => <Badge color="grey">{x.i + 1}</Badge>, width: 70 },
            { id: 'n', header: 'Name / identifier', cell: (x) => <Box fontSize="body-s"><code>{x.n}</code></Box> },
          ]}
          empty={<Box textAlign="center" color="text-body-secondary">No resource names were recorded for this row.</Box>}
        />
      </SpaceBetween>
    </Modal>
  );
}
