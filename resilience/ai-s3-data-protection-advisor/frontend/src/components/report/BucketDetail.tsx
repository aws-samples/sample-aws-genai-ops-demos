import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import { accountLabel, riskName } from '../../lib/format';
import { cellStatus, severityType } from '../../lib/status';
import type { BucketRow, RiskKey } from '../../lib/types';

const RISKS: RiskKey[] = ['he', 'ma', 'rd'];

export default function BucketDetail({ row }: { row: BucketRow }) {
  return (
    <SpaceBetween size="l">
      <Header variant="h2" description={`${accountLabel(row.account, row.account_id)}, ${row.region}`}>
        {row.bucket} {row.spotlight && <Badge color="red">Priority focus</Badge>}
      </Header>
      <Table
        variant="embedded"
        header={<Header variant="h3">Controls by threat</Header>}
        items={row.controls ?? []}
        columnDefinitions={[
          { id: 'control', header: 'Control', cell: (c) => <>{c.control}<Box variant="small" color="text-body-secondary">{c.value}</Box></> },
          ...RISKS.map((k) => ({
            id: k,
            header: riskName(k),
            cell: (c: NonNullable<BucketRow['controls']>[number]) => {
              const [state, note] = c.cells?.[k] ?? ['unknown', ''];
              const st = cellStatus(state);
              return <><StatusIndicator type={st.type}>{st.label}</StatusIndicator>{note && <Box variant="small" color="text-body-secondary">{note}</Box>}</>;
            },
          })),
        ]}
      />
      <ColumnLayout columns={2}>
        <SpaceBetween size="xs">
          <Header variant="h3">Evidence</Header>
          <ul>{(row.evidence ?? []).map((e, i) => <li key={i}><b>{e.label}:</b> {e.value}</li>)}</ul>
        </SpaceBetween>
        <SpaceBetween size="xs">
          <Header variant="h3">Why it is ranked here</Header>
          <ul>{(row.reasons ?? []).map((r, i) => <li key={i}>{r}</li>)}</ul>
          {row.vce && <Box>Versioning cost (estimate): {row.vce}</Box>}
        </SpaceBetween>
      </ColumnLayout>
      {(row.findings ?? []).length > 0 && (
        <SpaceBetween size="s">
          <Header variant="h3">Findings</Header>
          {(row.findings ?? []).map((f, i) => (
            <div key={i}>
              <StatusIndicator type={severityType(f.severity)}>{f.title}</StatusIndicator>
              <Box color="text-body-secondary">{f.description}</Box>
            </div>
          ))}
        </SpaceBetween>
      )}
    </SpaceBetween>
  );
}
