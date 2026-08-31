import { useState, useEffect } from 'react';
import {
  Table,
  Header,
  SpaceBetween,
  Box,
  Button,
  Badge,
  Pagination,
  TextFilter,
  Select,
  ColumnLayout,
  Link,
  Spinner,
  Alert,
  SelectProps,
} from '@cloudscape-design/components';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { UserInfo } from '../App';
import { Finding, PRIORITY_COLORS, STATUS_COLORS } from '../types';
import { getFindings } from '../services/api';

interface FindingsBacklogProps {
  userInfo: UserInfo | null;
}

const STATUS_OPTIONS: SelectProps.Option[] = [
  { value: '', label: 'All statuses' },
  { value: 'pending', label: 'Pending' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'rejected', label: 'Rejected' },
];

export default function FindingsBacklog({ userInfo }: FindingsBacklogProps) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  
  const [findings, setFindings] = useState<Finding[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterText, setFilterText] = useState('');
  const [statusFilter, setStatusFilter] = useState<SelectProps.Option>(
    STATUS_OPTIONS.find(opt => opt.value === searchParams.get('status')) || STATUS_OPTIONS[0]
  );
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 10;

  const isChampion = userInfo?.groups.includes('finops-admin') || userInfo?.groups.includes('champion');

  useEffect(() => {
    async function loadFindings() {
      setLoading(true);
      try {
        const params: { status?: string } = {};
        if (statusFilter.value) params.status = statusFilter.value;
        
        const data = await getFindings(params);
        setFindings(data.findings);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load findings');
      } finally {
        setLoading(false);
      }
    }
    loadFindings();
  }, [statusFilter]);

  // Update URL when filter changes
  useEffect(() => {
    if (statusFilter.value) {
      setSearchParams({ status: statusFilter.value });
    } else {
      setSearchParams({});
    }
  }, [statusFilter, setSearchParams]);

  const filteredFindings = findings.filter(finding => {
    if (!filterText) return true;
    const searchLower = filterText.toLowerCase();
    return (
      finding.title.toLowerCase().includes(searchLower) ||
      finding.service.toLowerCase().includes(searchLower) ||
      finding.category.toLowerCase().includes(searchLower) ||
      finding.description?.toLowerCase().includes(searchLower)
    );
  });

  const paginatedFindings = filteredFindings.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  );

  const totalPages = Math.ceil(filteredFindings.length / pageSize);

  if (loading) {
    return (
      <Box textAlign="center" padding="xxl">
        <Spinner size="large" />
        <Box variant="p" margin={{ top: 's' }}>Loading findings...</Box>
      </Box>
    );
  }

  if (error) {
    return (
      <Alert type="error" header="Error loading findings">
        {error}
      </Alert>
    );
  }

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Review and action FinOps Agent recommendations"
        counter={`(${filteredFindings.length})`}
      >
        Findings Backlog
      </Header>

      <ColumnLayout columns={4} variant="text-grid">
        <Box>
          <Box variant="awsui-key-label">Total Findings</Box>
          <Box fontSize="display-l">{findings.length}</Box>
        </Box>
        <Box>
          <Box variant="awsui-key-label">Pending Review</Box>
          <Box fontSize="display-l" color="text-status-info">
            {findings.filter(f => f.status === 'pending').length}
          </Box>
        </Box>
        <Box>
          <Box variant="awsui-key-label">Total Potential Savings</Box>
          <Box fontSize="display-l" color="text-status-success">
            ${findings.reduce((sum, f) => sum + f.estimatedSavingsUsd, 0).toLocaleString()}
          </Box>
        </Box>
        <Box>
          <Box variant="awsui-key-label">Pending Savings</Box>
          <Box fontSize="display-l" color="text-status-warning">
            ${findings.filter(f => f.status === 'pending').reduce((sum, f) => sum + f.estimatedSavingsUsd, 0).toLocaleString()}
          </Box>
        </Box>
      </ColumnLayout>

      <Table
        columnDefinitions={[
          {
            id: 'priority',
            header: 'Priority',
            cell: item => (
              <Badge color={PRIORITY_COLORS[item.priority] as 'red' | 'grey' | 'blue' | 'green'}>
                {item.priority.toUpperCase()}
              </Badge>
            ),
            width: 100,
            sortingField: 'priority',
          },
          {
            id: 'title',
            header: 'Title',
            cell: item => (
              <Link onFollow={(e) => { e.preventDefault(); navigate(`/findings/${item.findingId}`); }}>
                {item.title}
              </Link>
            ),
            sortingField: 'title',
          },
          {
            id: 'service',
            header: 'Service',
            cell: item => item.service,
            sortingField: 'service',
            width: 120,
          },
          {
            id: 'category',
            header: 'Category',
            cell: item => item.category,
            sortingField: 'category',
            width: 140,
          },
          {
            id: 'savings',
            header: 'Est. Savings',
            cell: item => (
              <Box color="text-status-success" fontWeight="bold">
                ${item.estimatedSavingsUsd.toLocaleString()}/mo
              </Box>
            ),
            sortingField: 'estimatedSavingsUsd',
            width: 130,
          },
          {
            id: 'status',
            header: 'Status',
            cell: item => (
              <Badge color={STATUS_COLORS[item.status] as 'blue' | 'green' | 'grey'}>
                {item.status}
              </Badge>
            ),
            width: 100,
          },
          {
            id: 'actions',
            header: 'Actions',
            cell: item => (
              item.status === 'pending' && isChampion ? (
                <Button
                  variant="primary"
                  onClick={() => navigate(`/findings/${item.findingId}`)}
                >
                  Review
                </Button>
              ) : (
                <Button
                  variant="link"
                  onClick={() => navigate(`/findings/${item.findingId}`)}
                >
                  View
                </Button>
              )
            ),
            width: 100,
          },
        ]}
        items={paginatedFindings}
        loading={loading}
        loadingText="Loading findings..."
        empty={
          <Box textAlign="center" padding="l">
            <Box variant="h3">No findings</Box>
            <Box variant="p">No findings match your current filters.</Box>
          </Box>
        }
        filter={
          <SpaceBetween direction="horizontal" size="m">
            <TextFilter
              filteringText={filterText}
              filteringPlaceholder="Search findings..."
              onChange={({ detail }) => {
                setFilterText(detail.filteringText);
                setCurrentPage(1);
              }}
            />
            <Select
              selectedOption={statusFilter}
              onChange={({ detail }) => {
                setStatusFilter(detail.selectedOption);
                setCurrentPage(1);
              }}
              options={STATUS_OPTIONS}
              placeholder="Filter by status"
            />
          </SpaceBetween>
        }
        pagination={
          <Pagination
            currentPageIndex={currentPage}
            pagesCount={totalPages}
            onChange={({ detail }) => setCurrentPage(detail.currentPageIndex)}
          />
        }
        sortingDisabled
        variant="full-page"
        stickyHeader
      />
    </SpaceBetween>
  );
}
