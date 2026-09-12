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
import Popover from '@cloudscape-design/components/popover';
import {
  statusMeta, getDeadline, formatDate, formatDaysLeft, serviceLabel, itemName,
  resourceCount, resourceDetails, resourceWord, healthFlagged, healthEventLabel, ResourceRef,
  costExposure, estimateFormula, formatUsd, HOURS_PER_MONTH, ExtendedSupportEstimate,
} from '../lifecycle';

// Why a resource has no Extended Support figure
const NO_ESTIMATE: Record<string, string> = {
  no_extended_support: 'No Extended Support for this engine: AWS upgrades it at end of standard support',
  no_price: 'No Extended Support price published for this version',
  unknown_instance_class: 'vCPU count unknown for this instance class',
  no_capacity: 'Serverless v2 capacity unknown',
  error: 'Could not estimate',
};

function EstimateCell({ e }: { e: ExtendedSupportEstimate }) {
  if (!e.eligible) {
    return <Box variant="small" color="text-body-secondary">{NO_ESTIMATE[e.reason || 'error'] || e.note || '-'}</Box>;
  }
  return (
    <SpaceBetween size="xxxs">
      <Popover dismissButton={false} position="left" size="large" triggerType="text"
        content={
          <SpaceBetween size="xs">
            <Box variant="strong">How this is calculated</Box>
            <Box variant="code">{estimateFormula(e)} = {formatUsd(e.monthly_yr1_2, 2)}/month</Box>
            {e.serverless && <Box variant="small">Serverless v2 bills per ACU actually used; {formatUsd(e.monthly_yr1_2_min, 2)} at min ACU ({e.min_acu}), {formatUsd(e.monthly_yr1_2, 2)} at max ACU ({e.max_acu}).</Box>}
            <Box variant="small">Year 3 rate ${e.price_yr3}/{e.unit === 'ACU-hour' ? 'ACU-h' : 'vCPU-h'} → {formatUsd(e.monthly_yr3, 2)}/month from {e.year3_start || 'n/a'}.</Box>
            <Box variant="small">
              Billed from {e.extended_support_start || 'a date not yet in the catalog'}
              {e.extended_support_end ? ` until ${e.extended_support_end}` : ''}. Next 12 months: {formatUsd(e.forecast_12m)}.
            </Box>
            <Box variant="small" color="text-body-secondary">
              List price from the AWS Price List API for this region ({e.price_source === 'sku' ? 'exact SKU for this version' : `no SKU for ${e.engine_family} ${e.major_version} yet: ${e.engine_family} family rate used`}),
              {' '}{HOURS_PER_MONTH} h/month (always on), no Reserved Instance or usage data. Surcharge on top of the normal instance price.
            </Box>
          </SpaceBetween>
        }>
        <Box variant="strong" color={e.in_extended_support ? 'text-status-error' : 'inherit'}>{formatUsd(e.monthly_yr1_2)}/mo</Box>
      </Popover>
      <Box variant="small" color="text-body-secondary">
        {e.in_extended_support ? 'billing now' : e.extended_support_start ? `from ${e.extended_support_start}` : 'start date unknown'}
        {e.price_source === 'family-estimate' ? ' · estimate' : ''}
      </Box>
    </SpaceBetween>
  );
}

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
  const flagged = healthFlagged(row);
  const exposure = costExposure(row);
  const showCost = resources.some((r) => r.extended_support);
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
          {exposure && exposure.resources_priced > 0 && (
            <Field label="Extended Support exposure">
              <Box variant="strong">{formatUsd(exposure.monthly)}/month</Box>
              <Box variant="small" color="text-body-secondary">
                {formatUsd(exposure.forecast_12m)} over the next 12 months · {exposure.resources_priced} of {exposure.resources_total} priced
                {exposure.in_extended_support ? ` · ${exposure.in_extended_support} billing now` : ''}
              </Box>
            </Field>
          )}
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
              description={[
                flagged ? `${flagged} named in an open AWS Health notice.` : '',
                resources.length < count ? `Showing the first ${resources.length}; the scan stores a capped list.` : '',
              ].filter(Boolean).join(' ') || undefined}
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
            ...(showCost ? [
              {
                id: 'class', header: 'Class', width: 150, cell: (r: ResourceRef) => {
                  const e = r.extended_support;
                  if (!e?.instance_class) return <Box color="text-body-secondary">-</Box>;
                  return (
                    <SpaceBetween size="xxxs">
                      <Box>{e.instance_class}</Box>
                      <Box variant="small" color="text-body-secondary">
                        {e.serverless ? `${e.min_acu ?? 0}–${e.max_acu ?? 0} ACU` : e.vcpus !== undefined ? `${e.vcpus} vCPU` : ''}
                        {e.multi_az ? ' · Multi-AZ (×2)' : ''}
                      </Box>
                    </SpaceBetween>
                  );
                },
              },
              {
                id: 'cost', header: 'Extended Support', width: 190, cell: (r: ResourceRef) =>
                  r.extended_support ? <EstimateCell e={r.extended_support} /> : <Box color="text-body-secondary">-</Box>,
              },
            ] : []),
            {
              id: 'health', header: 'AWS Health', width: 220, cell: (r) => {
                const h = r.health;
                if (!h) return <Box color="text-body-secondary">-</Box>;
                const resolved = h.entity_status === 'RESOLVED';
                return (
                  <SpaceBetween size="xxxs">
                    <StatusIndicator type={resolved ? 'success' : 'warning'}>{resolved ? 'Resolved by AWS' : 'Flagged by AWS'}</StatusIndicator>
                    <Box variant="small">
                      {h.console_url
                        ? <Link href={h.console_url} external fontSize="body-s">{healthEventLabel(h.event_type)}</Link>
                        : healthEventLabel(h.event_type)}
                    </Box>
                  </SpaceBetween>
                );
              },
            },
            {
              id: 'console', header: 'Console', width: 110, cell: (r) => r.console_url
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
