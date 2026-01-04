import { useState, useEffect } from 'react';
import Table from '@cloudscape-design/components/table';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import Alert from '@cloudscape-design/components/alert';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Badge from '@cloudscape-design/components/badge';
import TextFilter from '@cloudscape-design/components/text-filter';
import Select from '@cloudscape-design/components/select';
import Pagination from '@cloudscape-design/components/pagination';
import { getDeprecations, DeprecationItem } from '../api';

const ITEMS_PER_PAGE = 20;

export default function Deprecations() {
  const [items, setItems] = useState<DeprecationItem[]>([]);
  const [filteredItems, setFilteredItems] = useState<DeprecationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filterText, setFilterText] = useState('');
  const [statusFilter, setStatusFilter] = useState<any>({ value: 'all' });
  const [currentPageIndex, setCurrentPageIndex] = useState(1);

  useEffect(() => {
    loadDeprecations();
  }, []);

  useEffect(() => {
    applyFilters();
  }, [items, filterText, statusFilter]);

  const loadDeprecations = async () => {
    try {
      setLoading(true);
      const data = await getDeprecations();
      setItems(data);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const applyFilters = () => {
    let filtered = [...items];

    // Text filter
    if (filterText) {
      const lowerFilter = filterText.toLowerCase();
      filtered = filtered.filter(item =>
        item.service_name.toLowerCase().includes(lowerFilter) ||
        item.item_id.toLowerCase().includes(lowerFilter) ||
        JSON.stringify(item.service_specific).toLowerCase().includes(lowerFilter)
      );
    }

    // Status filter
    if (statusFilter.value !== 'all') {
      filtered = filtered.filter(item => item.status === statusFilter.value);
    }

    setFilteredItems(filtered);
    setCurrentPageIndex(1); // Reset to first page when filters change
  };

  // Get paginated items
  const paginatedItems = filteredItems.slice(
    (currentPageIndex - 1) * ITEMS_PER_PAGE,
    currentPageIndex * ITEMS_PER_PAGE
  );

  const getStatusIndicator = (status: string) => {
    switch (status) {
      case 'deprecated':
        return <StatusIndicator type="warning">Deprecated</StatusIndicator>;
      case 'extended_support':
        return <StatusIndicator type="info">Extended Support</StatusIndicator>;
      case 'end_of_life':
        return <StatusIndicator type="error">End of Life</StatusIndicator>;
      case 'end_of_support_date':
        return <StatusIndicator type="warning">End of Support Date</StatusIndicator>;
      default:
        return <StatusIndicator>{status}</StatusIndicator>;
    }
  };

  return (
    <SpaceBetween size="l">
      {error && (
        <Alert type="error" dismissible onDismiss={() => setError('')}>
          {error}
        </Alert>
      )}

      <Table
        columnDefinitions={[
          {
            id: 'service',
            header: 'Service',
            cell: (item) => (
              <Badge color="blue">{item.service_name.toUpperCase()}</Badge>
            ),
            sortingField: 'service_name',
          },
          {
            id: 'name',
            header: 'Name',
            cell: (item) => (
              <SpaceBetween size="xxxs">
                <Box variant="strong">
                  {item.service_specific.name || item.item_id}
                </Box>
                <Box variant="small" color="text-body-secondary">
                  {item.service_specific.identifier || ''}
                </Box>
              </SpaceBetween>
            ),
          },
          {
            id: 'status',
            header: 'Status',
            cell: (item) => getStatusIndicator(item.status),
            sortingField: 'status',
          },
          {
            id: 'dates',
            header: 'Key Dates',
            cell: (item) => {
              const dateElements: JSX.Element[] = [];
              
              // Define date field mappings with display labels
              const dateFieldMappings = [
                { field: 'deprecation_date', label: 'Deprecated' },
                { field: 'end_of_support_date', label: 'End of Support' },
                { field: 'end_of_standard_support_date', label: 'End of Standard Support' },
                { field: 'end_of_extended_support_date', label: 'End of Extended Support' },
                { field: 'retirement_date', label: 'Retired' },
                { field: 'target_retirement_date', label: 'Target Retirement' },
                { field: 'block_create_date', label: 'Block Create' },
                { field: 'block_update_date', label: 'Block Update' },
                { field: 'upstream_release_date', label: 'Upstream Release' },
                { field: 'eks_release_date', label: 'EKS Release' },
                { field: 'community_release_date', label: 'Community Release' },
                { field: 'rds_release_date', label: 'RDS Release' },
                { field: 'msk_release_date', label: 'MSK Release' },
                { field: 'start_extended_support_y1_date', label: 'Extended Support Y1' },
                { field: 'start_extended_support_y2_date', label: 'Extended Support Y2' },
                { field: 'start_extended_support_y3_date', label: 'Extended Support Y3' },
              ];

              // Process each date field mapping
              dateFieldMappings.forEach(({ field, label }) => {
                if (item.service_specific[field]) {
                  dateElements.push(
                    <Box key={field} variant="small">
                      <strong>{label}:</strong> {item.service_specific[field]}
                    </Box>
                  );
                }
              });

              return (
                <SpaceBetween size="xxxs">
                  {dateElements.length > 0 ? dateElements : <Box variant="small" color="text-body-secondary">No dates available</Box>}
                </SpaceBetween>
              );
            },
          },
          {
            id: 'extracted',
            header: 'Last Verified',
            cell: (item) => (
              <Box variant="small">
                {new Date(item.last_verified).toLocaleDateString()}
              </Box>
            ),
            sortingField: 'last_verified',
          },
        ]}
        items={paginatedItems}
        loading={loading}
        loadingText="Loading deprecation items..."
        empty={
          <Box textAlign="center" color="inherit">
            <Box padding={{ bottom: 's' }} variant="p" color="inherit">
              No deprecation items found
            </Box>
          </Box>
        }
        filter={
          <div style={{ display: 'flex', gap: '16px' }}>
            <TextFilter
              filteringText={filterText}
              filteringPlaceholder="Search deprecations..."
              filteringAriaLabel="Filter deprecations"
              onChange={({ detail }) => setFilterText(detail.filteringText)}
            />
            <Select
              selectedOption={statusFilter}
              onChange={({ detail }) => setStatusFilter(detail.selectedOption)}
              options={[
                { label: 'All Statuses', value: 'all' },
                { label: 'Deprecated', value: 'deprecated' },
                { label: 'Extended Support', value: 'extended_support' },
                { label: 'End of Life', value: 'end_of_life' },
              ]}
              selectedAriaLabel="Selected"
            />
          </div>
        }
        header={
          <Header
            variant="h1"
            counter={`(${filteredItems.length})`}
            description="Browse all AWS service deprecation items across monitored services"
          >
            Deprecations
          </Header>
        }
        pagination={
          <Pagination
            currentPageIndex={currentPageIndex}
            onChange={({ detail }) => setCurrentPageIndex(detail.currentPageIndex)}
            pagesCount={Math.ceil(filteredItems.length / ITEMS_PER_PAGE)}
          />
        }
      />
    </SpaceBetween>
  );
}
