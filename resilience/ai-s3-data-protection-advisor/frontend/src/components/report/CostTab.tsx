import Box from '@cloudscape-design/components/box';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import SpaceBetween from '@cloudscape-design/components/space-between';
import type { CostOptimizationTab as T } from '../../lib/types';

export default function CostTab({ tab }: { tab: T }) {
  const items = tab.items ?? [];
  return (
    <SpaceBetween size="m">
      {items.length === 0 && <Box>No cost optimization items.</Box>}
      {items.map((it, i) => (
        <Container key={i} header={<Header variant="h3">{it.title}</Header>}>
          <SpaceBetween size="s">
            <KeyValuePairs
              columns={2}
              items={[
                { label: 'Estimated effect', value: it.savings },
                { label: 'Effort', value: it.effort },
                ...(it.magnitude?.label ? [{ label: 'Order of magnitude', value: it.magnitude.label }] : []),
              ]}
            />
            <Box>{it.description}</Box>
            {it.basis && <Box color="text-body-secondary">Basis (estimate): {it.basis}</Box>}
            {(it.resources ?? []).length > 0 && <ul>{(it.resources ?? []).map((r, j) => <li key={j}>{r}</li>)}</ul>}
          </SpaceBetween>
        </Container>
      ))}
    </SpaceBetween>
  );
}
