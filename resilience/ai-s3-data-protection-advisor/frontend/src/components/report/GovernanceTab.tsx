import Box from '@cloudscape-design/components/box';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import { severityType } from '../../lib/status';
import type { GovernanceTab as T } from '../../lib/types';

export default function GovernanceTab({ tab }: { tab: T }) {
  return (
    <SpaceBetween size="m">
      <Container header={<Header variant="h2">Overall maturity: level {tab.overall_level} of {(tab.levels ?? []).length || 5} ({tab.overall_label}), score {tab.overall ?? 'Unconfirmed'}</Header>}>
        <SpaceBetween size="m">
          <Box>{tab.summary}</Box>
          <ul>
            {(tab.levels ?? []).map((l) => (
              <li key={l.level}>{l.level === tab.overall_level ? <b>L{l.level} {l.label}: {l.description}</b> : <>L{l.level} {l.label}: {l.description}</>}</li>
            ))}
          </ul>
        </SpaceBetween>
      </Container>
      <Table
        variant="container"
        header={<Header variant="h2">Dimensions</Header>}
        items={tab.dimensions ?? []}
        columnDefinitions={[
          { id: 'n', header: 'Dimension', cell: (d) => d.name },
          { id: 'l', header: 'Level', cell: (d) => `L${d.level}` },
          { id: 'c', header: 'Current', cell: (d) => d.current },
          { id: 'x', header: 'Next', cell: (d) => d.next },
          { id: 'e', header: 'Evidence', cell: (d) => <ul>{(d.evidence ?? []).map((e, i) => <li key={i}>{e}</li>)}</ul> },
        ]}
      />
      <Table
        variant="container"
        header={<Header variant="h2">Policies</Header>}
        items={tab.policies ?? []}
        columnDefinitions={[
          { id: 'r', header: 'Rule', cell: (p) => <>{p.rule}{p.why && <Box variant="small" color="text-body-secondary">{p.why}</Box>}</> },
          { id: 's', header: 'In scope', cell: (p) => p.scope ?? '-' },
          { id: 'c', header: 'Compliant', cell: (p) => `${p.compliant ?? 0} / ${p.total ?? 0}` },
          { id: 'p', header: 'Compliance', cell: (p) => (p.pct === null || p.pct === undefined ? <StatusIndicator type="pending">Not assessed</StatusIndicator> : `${p.pct}%`) },
          { id: 'f', header: 'Failing buckets', cell: (p) => (p.failing ?? []).join(', ') || '-' },
        ]}
      />
      <Container header={<Header variant="h2">Findings</Header>}>
        <SpaceBetween size="m">
          {(tab.findings ?? []).map((f, i) => (
            <div key={i}>
              <StatusIndicator type={severityType(f.severity)}>{f.title}</StatusIndicator>
              <Box>{f.evidence}</Box>
              <Box color="text-body-secondary">{f.improvement}</Box>
              {f.dimension && <Box variant="small" color="text-body-secondary">Dimension: {f.dimension}</Box>}
            </div>
          ))}
          {(tab.findings ?? []).length === 0 && <Box>No governance findings.</Box>}
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
}
