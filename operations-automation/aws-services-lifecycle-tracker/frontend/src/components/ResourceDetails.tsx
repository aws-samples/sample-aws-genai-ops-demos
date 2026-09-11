// Details view for one inventory row: the only place the UI lists resources
// (tables show counts only, see #141). Each resource shows its name, ARN and
// a deep link to the AWS console page.
import { useMemo, useState } from 'react';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import CopyToClipboard from '@cloudscape-design/components/copy-to-clipboard';
import Link from '@cloudscape-design/components/link';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import TextFilter from '@cloudscape-design/components/text-filter';
import Header from '@cloudscape-design/components/header';
import type { DeprecationItem, ActionPlan } from '../api';
import {
  statusMeta, getDeadline, formatDate, formatDaysLeft, serviceLabel, itemName,
  resourceCount, resourceDetails, resourceWord, ResourceRef,
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
  const resources = useMemo<ResourceRef[]>(() => (row ? resourceDetails(row) : []), [row]);
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? resources.filter((r) => `${r.name} ${r.arn || ''}`.toLowerCase().includes(q)) : resources;
  }, [resources, filter]);

  if (!row) return null;
  const m = statusMeta(row.status);
  const d = getDeadline(row);
  const count = resourceCount(row);
  const hasArns = resources.some((r) => r.arn);
  const dates = fact
    ? Object.entries(fact.service_specific || {}).filter(([k, v]) => k.endsWith('_date') && v && v !== 'N/A')
    : [];

  return (
    <Modal
      visible
      size="max"
      onDismiss={onDismiss}
      header={`${serviceLabel(row.service_name)} · ${itemName(row)}`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <CopyToClipboard
              variant="button"
              copyButtonText={hasArns ? 'Copy ARNs' : 'Copy names'}
              copySuccessText="Copied"
              copyErrorText="Copy failed"
              textToCopy={shown.map((r) => r.arn || r.name).join('\n')}
            />
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
          items={shown}
          trackBy="name"
          wrapLines
          header={
            <Header
              variant="h3"
              counter={`(${count})`}
              description={resources.length < count ? `Showing the first ${resources.length}; the scan stores a capped list.` : undefined}
            >
              {resourceWord(count).replace(/^r/, 'R')}
            </Header>
          }
          filter={
            resources.length > 10 ? (
              <TextFilter filteringText={filter} filteringPlaceholder="Find by name or ARN" filteringAriaLabel="Filter resources"
                countText={`${shown.length} match${shown.length === 1 ? '' : 'es'}`}
                onChange={({ detail }) => setFilter(detail.filteringText)} />
            ) : undefined
          }
          columnDefinitions={[
            {
              id: 'name', header: 'Name', cell: (r) => r.console_url
                ? <Link href={r.console_url} external>{r.name}</Link>
                : <Box>{r.name}</Box>,
            },
            {
              id: 'arn', header: 'ARN', cell: (r) => r.arn
                ? <CopyToClipboard variant="inline" textToCopy={r.arn} copyButtonAriaLabel={`Copy ARN of ${r.name}`} copySuccessText="Copied" copyErrorText="Copy failed" />
                : <Box color="text-body-secondary">-</Box>,
            },
            {
              id: 'console', header: 'Console', width: 130, cell: (r) => r.console_url
                ? <Link href={r.console_url} external fontSize="body-s">Open</Link>
                : <Box color="text-body-secondary">-</Box>,
            },
          ]}
          empty={<Box textAlign="center" color="text-body-secondary">No resources were recorded for this row.</Box>}
        />
        {!hasArns && resources.length > 0 && (
          <Box variant="small" color="text-body-secondary">ARNs and console links appear after the next account scan.</Box>
        )}
      </SpaceBetween>
    </Modal>
  );
}
