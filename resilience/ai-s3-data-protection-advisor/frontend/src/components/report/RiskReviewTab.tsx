import Box from '@cloudscape-design/components/box';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { severityType } from '../../lib/status';
import type { OperationalReviewTab as T } from '../../lib/types';

export default function RiskReviewTab({ tab }: { tab: T }) {
  return (
    <SpaceBetween size="m">
      {tab.description && <Box>{tab.description}</Box>}
      {(tab.resources ?? []).map((r, i) => (
        <SpaceBetween size="m" key={i}>
          {(tab.resources ?? []).length > 1 && <Header variant="h3" description={r.region}>{r.name}</Header>}
          {(r.pillars ?? []).map((p, j) => (
            <Container key={j} header={<Header variant="h3" counter={`(${p.pass ?? 0} of ${p.total ?? 0})`}>{p.name}</Header>}>
              <SpaceBetween size="xs">
                {(p.checks ?? []).map((c, k) => (
                  <div key={k}>
                    <StatusIndicator type={c.pass ? 'success' : severityType(c.severity ?? 'LOW')}>{c.label}</StatusIndicator>
                    {c.description && <Box variant="small" color="text-body-secondary" margin={{ left: 'l' }}>{c.description}</Box>}
                  </div>
                ))}
              </SpaceBetween>
            </Container>
          ))}
        </SpaceBetween>
      ))}
    </SpaceBetween>
  );
}
