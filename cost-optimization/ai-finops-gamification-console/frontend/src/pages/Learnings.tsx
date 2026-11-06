import { useState, useEffect } from 'react';
import {
  Table,
  Header,
  SpaceBetween,
  Box,
  Badge,
  Select,
  SelectProps,
  TextFilter,
  ColumnLayout,
  Spinner,
  Alert,
  Container,
  ExpandableSection,
  Link,
} from '@cloudscape-design/components';
import { useNavigate } from 'react-router-dom';
import { Learning } from '../types';
import { getLearnings } from '../services/api';

const SERVICE_OPTIONS: SelectProps.Option[] = [
  { value: '', label: 'All services' },
  { value: 'EC2', label: 'Amazon EC2' },
  { value: 'RDS', label: 'Amazon RDS' },
  { value: 'S3', label: 'Amazon S3' },
  { value: 'Lambda', label: 'AWS Lambda' },
  { value: 'EBS', label: 'Amazon EBS' },
  { value: 'ELB', label: 'Elastic Load Balancing' },
  { value: 'CloudWatch', label: 'Amazon CloudWatch' },
  { value: 'DynamoDB', label: 'Amazon DynamoDB' },
  { value: 'ElastiCache', label: 'Amazon ElastiCache' },
  { value: 'Other', label: 'Other Services' },
];

export default function Learnings() {
  const navigate = useNavigate();
  const [learnings, setLearnings] = useState<Learning[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterText, setFilterText] = useState('');
  const [serviceFilter, setServiceFilter] = useState<SelectProps.Option>(SERVICE_OPTIONS[0]);

  useEffect(() => {
    async function loadLearnings() {
      setLoading(true);
      try {
        const data = await getLearnings(serviceFilter.value || undefined);
        setLearnings(data.learnings);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load learnings');
      } finally {
        setLoading(false);
      }
    }
    loadLearnings();
  }, [serviceFilter]);

  const filteredLearnings = learnings.filter(learning => {
    if (!filterText) return true;
    const searchLower = filterText.toLowerCase();
    return (
      learning.title.toLowerCase().includes(searchLower) ||
      learning.service.toLowerCase().includes(searchLower) ||
      learning.category.toLowerCase().includes(searchLower) ||
      learning.description?.toLowerCase().includes(searchLower) ||
      learning.rejectionReason?.toLowerCase().includes(searchLower)
    );
  });

  const acceptanceLearnings = filteredLearnings.filter(l => l.type === 'acceptance');
  const rejectionLearnings = filteredLearnings.filter(l => l.type === 'rejection');

  // Get unique categories for summary
  const categoryStats = filteredLearnings.reduce((acc, l) => {
    const key = l.type === 'rejection' ? (l.rejectionReason || 'Unknown') : 'Accepted';
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  if (loading) {
    return (
      <Box textAlign="center" padding="xxl">
        <Spinner size="large" />
        <Box variant="p" margin={{ top: 's' }}>Loading learnings...</Box>
      </Box>
    );
  }

  if (error) {
    return (
      <Alert type="error" header="Error loading learnings">
        {error}
      </Alert>
    );
  }

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Patterns and insights from past finding decisions to improve future recommendations"
        counter={`(${filteredLearnings.length})`}
      >
        Learnings
      </Header>

      <ColumnLayout columns={4} variant="text-grid">
        <Box>
          <Box variant="awsui-key-label">Total Learnings</Box>
          <Box fontSize="display-l">{filteredLearnings.length}</Box>
        </Box>
        <Box>
          <Box variant="awsui-key-label">From Acceptances</Box>
          <Box fontSize="display-l" color="text-status-success">
            {acceptanceLearnings.length}
          </Box>
        </Box>
        <Box>
          <Box variant="awsui-key-label">From Rejections</Box>
          <Box fontSize="display-l" color="text-status-warning">
            {rejectionLearnings.length}
          </Box>
        </Box>
        <Box>
          <Box variant="awsui-key-label">Unique Services</Box>
          <Box fontSize="display-l">
            {new Set(filteredLearnings.map(l => l.service)).size}
          </Box>
        </Box>
      </ColumnLayout>

      <Container header={<Header variant="h2">Feedback Categories</Header>}>
        <ColumnLayout columns={4} variant="text-grid">
          {Object.entries(categoryStats)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 8)
            .map(([category, count]) => (
              <Box key={category}>
                <Box variant="awsui-key-label">{category}</Box>
                <Box fontSize="heading-l">{count}</Box>
              </Box>
            ))}
        </ColumnLayout>
      </Container>

      <SpaceBetween direction="horizontal" size="m">
        <TextFilter
          filteringText={filterText}
          filteringPlaceholder="Search learnings..."
          onChange={({ detail }) => setFilterText(detail.filteringText)}
        />
        <Select
          selectedOption={serviceFilter}
          onChange={({ detail }) => setServiceFilter(detail.selectedOption)}
          options={SERVICE_OPTIONS}
          placeholder="Filter by service"
        />
      </SpaceBetween>

      {rejectionLearnings.length > 0 && (
        <ExpandableSection
          header={
            <SpaceBetween direction="horizontal" size="xs">
              <Badge color="grey">Rejection Insights</Badge>
              <span>({rejectionLearnings.length})</span>
            </SpaceBetween>
          }
          defaultExpanded
        >
          <Table
            columnDefinitions={[
              {
                id: 'service',
                header: 'Service',
                cell: item => <Badge>{item.service}</Badge>,
                width: 120,
              },
              {
                id: 'title',
                header: 'Finding Title',
                cell: item => (
                  <Link onFollow={(e) => { e.preventDefault(); navigate(`/findings/${item.findingId}`); }}>
                    {item.title}
                  </Link>
                ),
              },
              {
                id: 'category',
                header: 'Category',
                cell: item => item.category,
                width: 140,
              },
              {
                id: 'reason',
                header: 'Rejection Reason',
                cell: item => item.rejectionReason || '-',
              },
              {
                id: 'feedback',
                header: 'Feedback',
                cell: item => item.detailedFeedback || item.suggestedImprovement || '-',
              },
              {
                id: 'created',
                header: 'Recorded',
                cell: item => new Date(item.createdAt).toLocaleDateString(),
                width: 110,
              },
            ]}
            items={rejectionLearnings}
            variant="embedded"
            empty={
              <Box textAlign="center" padding="l">
                <Box variant="p">No rejection learnings found.</Box>
              </Box>
            }
          />
        </ExpandableSection>
      )}

      {acceptanceLearnings.length > 0 && (
        <ExpandableSection
          header={
            <SpaceBetween direction="horizontal" size="xs">
              <Badge color="green">Acceptance Insights</Badge>
              <span>({acceptanceLearnings.length})</span>
            </SpaceBetween>
          }
          defaultExpanded
        >
          <Table
            columnDefinitions={[
              {
                id: 'service',
                header: 'Service',
                cell: item => <Badge color="blue">{item.service}</Badge>,
                width: 120,
              },
              {
                id: 'title',
                header: 'Finding Title',
                cell: item => (
                  <Link onFollow={(e) => { e.preventDefault(); navigate(`/findings/${item.findingId}`); }}>
                    {item.title}
                  </Link>
                ),
              },
              {
                id: 'category',
                header: 'Category',
                cell: item => item.category,
                width: 140,
              },
              {
                id: 'savings',
                header: 'Savings',
                cell: item => item.estimatedSavingsUsd ? (
                  <Box color="text-status-success" fontWeight="bold">
                    ${item.estimatedSavingsUsd.toLocaleString()}/mo
                  </Box>
                ) : '-',
                width: 120,
              },
              {
                id: 'implementation',
                header: 'Implementation Notes',
                cell: item => item.implementationDetails || '-',
              },
              {
                id: 'created',
                header: 'Recorded',
                cell: item => new Date(item.createdAt).toLocaleDateString(),
                width: 110,
              },
            ]}
            items={acceptanceLearnings}
            variant="embedded"
            empty={
              <Box textAlign="center" padding="l">
                <Box variant="p">No acceptance learnings found.</Box>
              </Box>
            }
          />
        </ExpandableSection>
      )}

      {filteredLearnings.length === 0 && (
        <Box textAlign="center" padding="xxl">
          <Box variant="h3">No learnings yet</Box>
          <Box variant="p">
            Learnings are recorded when findings are accepted or rejected with feedback.
            Start reviewing findings to build your knowledge base.
          </Box>
        </Box>
      )}
    </SpaceBetween>
  );
}
