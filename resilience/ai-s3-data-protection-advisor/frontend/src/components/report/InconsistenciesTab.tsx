import Box from '@cloudscape-design/components/box';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import { severityType, unconfirmedText } from '../../lib/status';
import type { InconsistenciesTab as T } from '../../lib/types';

export default function InconsistenciesTab({ tab }: { tab: T }) {
  const items = tab.items ?? [];
  return (
    <SpaceBetween size="m">
      <Box>{tab.families_compared ?? 0} families compared.</Box>
      {items.length === 0 && <Box>No inconsistencies found.</Box>}
      {items.map((it, i) => (
        <Container key={i} header={<Header variant="h3" description={it.basis}>{it.family}</Header>}>
          <SpaceBetween size="m">
            <KeyValuePairs
              columns={3}
              items={[
                { label: 'Setting', value: it.setting },
                { label: 'Severity', value: <StatusIndicator type={severityType(it.severity)}>{it.severity}</StatusIndicator> },
                { label: 'Align to', value: it.align_to ?? 'Not determined' },
              ]}
            />
            <Table
              variant="embedded"
              items={it.members ?? []}
              columnDefinitions={[
                { id: 'a', header: 'Account', cell: (m) => m.account },
                { id: 'b', header: 'Bucket', cell: (m) => m.bucket },
                { id: 'r', header: 'Region', cell: (m) => m.region },
                { id: 'v', header: 'Value', cell: (m) => unconfirmedText(m.value) },
              ]}
            />
            {(it.weaker ?? []).length > 0 && <Box>Weaker: {(it.weaker ?? []).join(', ')}</Box>}
            <Box>{it.recommendation}</Box>
          </SpaceBetween>
        </Container>
      ))}
      {(tab.consistent_families ?? []).length > 0 && (
        <Container header={<Header variant="h3">Consistent families</Header>}>
          <ul>{(tab.consistent_families ?? []).map((f, i) => <li key={i}>{f.family}: {f.note}</li>)}</ul>
        </Container>
      )}
      {tab.note && <Box color="text-body-secondary">{tab.note}</Box>}
    </SpaceBetween>
  );
}
