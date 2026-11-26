import { useCollection } from '@cloudscape-design/collection-hooks';
import Box from '@cloudscape-design/components/box';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Header from '@cloudscape-design/components/header';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import TextFilter from '@cloudscape-design/components/text-filter';
import { severityType } from '../../lib/status';
import type { RecommendationItem, RecommendationsTab as T } from '../../lib/types';

export default function RecommendationsTab({ tab }: { tab: T }) {
  const items = tab.items ?? [];
  const { items: rows, filterProps, filteredItemsCount, collectionProps } = useCollection<RecommendationItem>(items, {
    filtering: { empty: 'No recommendations', noMatch: 'No recommendations match the filter' },
  });
  return (
    <Table
      {...collectionProps}
      variant="container"
      header={<Header variant="h2" counter={`(${items.length})`}>Recommendations</Header>}
      items={rows}
      filter={<TextFilter {...filterProps} filteringPlaceholder="Find recommendations" countText={`${filteredItemsCount ?? 0} matches`} />}
      columnDefinitions={[
        { id: 'sev', header: 'Severity', cell: (r) => <StatusIndicator type={severityType(r.severity)}>{r.severity ?? 'Unconfirmed'}</StatusIndicator> },
        { id: 'bucket', header: 'Bucket', cell: (r) => r.bucket },
        { id: 'account', header: 'Account', cell: (r) => r.account_name || r.account },
        { id: 'region', header: 'Region', cell: (r) => r.region },
        { id: 'size', header: 'Size', cell: (r) => r.size_human ?? '-' },
        { id: 'rec', header: 'Recommendation', cell: (r) => r.recommendation ?? r.title },
        {
          id: 'why',
          header: 'Why',
          cell: (r) => (
            <ExpandableSection variant="footer" headerText={r.why ?? 'Details'}>
              {r.detail ?? r.description}
              {r.inconsistency && <Box variant="small" color="text-body-secondary">Related inconsistency: {r.inconsistency}</Box>}
            </ExpandableSection>
          ),
        },
      ]}
      empty="No recommendations"
    />
  );
}
