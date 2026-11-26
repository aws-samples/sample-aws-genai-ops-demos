import Box from '@cloudscape-design/components/box';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import type { InstructionsTab as T } from '../../lib/types';

export default function InstructionsTab({ tab }: { tab: T }) {
  return (
    <SpaceBetween size="m">
      <Header variant="h2" description={tab.intro}>{tab.heading}</Header>
      {(tab.sections ?? []).map((s, i) => (
        <Container key={i} header={<Header variant="h3">{s.title}</Header>}>
          <SpaceBetween size="s">
            {(s.items ?? []).length > 0 && <ul>{(s.items ?? []).map((it, j) => <li key={j}>{it}</li>)}</ul>}
            {s.table && (
              <Table
                variant="embedded"
                items={s.table.rows ?? []}
                columnDefinitions={(s.table.columns ?? []).map((c, ci) => ({ id: String(ci), header: c, cell: (r: string[]) => r[ci] ?? '' }))}
              />
            )}
            {s.note && <Box color="text-body-secondary">{s.note}</Box>}
          </SpaceBetween>
        </Container>
      ))}
    </SpaceBetween>
  );
}
