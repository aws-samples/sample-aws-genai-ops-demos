import Box from '@cloudscape-design/components/box';
import Popover from '@cloudscape-design/components/popover';
import StatusIndicator, { StatusIndicatorProps } from '@cloudscape-design/components/status-indicator';
import Icon from '@cloudscape-design/components/icon';
import SpaceBetween from '@cloudscape-design/components/space-between';
import { AccountHealthStatus, SupportTier } from '../api';

// Support tier of a scanned account and what it means for the results (#144).
// The tier is inferred from support:DescribeSeverityLevels, the method AWS
// documents (no API returns the plan). Several plans share a ceiling since the
// Dec 2025 lineup, so tiers are shown, not plan names.

const TIER_META: Record<SupportTier, { label: string; indicator: StatusIndicatorProps.Type; plans: string }> = {
  enterprise: { label: 'Enterprise tier', indicator: 'success', plans: 'Enterprise Support, Enterprise On-Ramp or Unified Operations' },
  business: { label: 'Business tier', indicator: 'success', plans: 'Business or Business Support+' },
  developer: { label: 'Developer', indicator: 'warning', plans: 'Developer Support' },
  basic: { label: 'Basic', indicator: 'warning', plans: 'Basic Support (free)' },
  unknown: { label: 'Unknown', indicator: 'pending', plans: 'could not be determined' },
};

export const SUPPORT_TIER_HELP =
  'Inferred from the case severities the account may open (support:DescribeSeverityLevels), the method AWS documents; ' +
  'no API returns the plan itself. It matters here because the AWS Health API only answers for Business Support+ / Enterprise tiers: ' +
  'resources in Basic or Developer accounts are matched against the catalog but never cross-checked with AWS Health notices, ' +
  'so an empty Health badge there means "not checked", not "nothing planned".';

/** Column header with the (i) explanation. */
export function SupportTierHeader() {
  return (
    <SpaceBetween direction="horizontal" size="xxs" alignItems="center">
      <span>Support plan</span>
      <Popover dismissButton={false} position="top" size="large" triggerType="custom"
        header="Why this is shown"
        content={<Box variant="p">{SUPPORT_TIER_HELP}</Box>}>
        <span role="button" tabIndex={0} aria-label="Why the Support plan is shown" style={{ cursor: 'pointer', display: 'inline-flex' }}>
          <Icon name="status-info" size="small" variant="link" />
        </span>
      </Popover>
    </SpaceBetween>
  );
}

/** Cell: tier badge + what Health did for this account. */
export function SupportTierCell({ status }: { status?: AccountHealthStatus }) {
  if (!status) return <Box color="text-body-secondary">-</Box>;
  const meta = TIER_META[status.tier || 'unknown'];
  return (
    <SpaceBetween size="xxxs">
      <Popover dismissButton={false} position="top" size="medium" triggerType="text"
        content={
          <SpaceBetween size="xxs">
            <Box variant="small">{meta.plans}</Box>
            {status.reason && <Box variant="small" color="text-body-secondary">{status.reason}</Box>}
          </SpaceBetween>
        }>
        <StatusIndicator type={meta.indicator}>{meta.label}</StatusIndicator>
      </Popover>
      <Box variant="small" color={status.health_available ? 'text-body-secondary' : 'text-status-warning'}>
        {status.health_available
          ? `Health: ${status.events} notice${status.events === 1 ? '' : 's'}, ${status.flagged} flagged`
          : 'Health not checked'}
      </Box>
    </SpaceBetween>
  );
}
