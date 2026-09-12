import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Button from '@cloudscape-design/components/button';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Table from '@cloudscape-design/components/table';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Spinner from '@cloudscape-design/components/spinner';
import {
  getServices,
  getDeprecations,
  ServiceConfig,
  DeprecationItem,
} from '../api';
import { statusMeta } from '../lifecycle';

export default function ServiceDetail() {
  const { serviceName } = useParams<{ serviceName: string }>();
  const navigate = useNavigate();

  const [service, setService] = useState<ServiceConfig | null>(null);
  const [deprecations, setDeprecations] = useState<DeprecationItem[]>([]);
  const [loadingService, setLoadingService] = useState(true);
  const [loadingDeprecations, setLoadingDeprecations] = useState(true);

  useEffect(() => {
    if (serviceName) {
      loadServiceData();
    }
  }, [serviceName]);

  const loadServiceData = async () => {
    await Promise.all([
      loadService(),
      loadDeprecations(),
    ]);
  };

  const loadService = async () => {
    try {
      setLoadingService(true);
      const services = await getServices();
      const found = services.find((s) => s.service_name === serviceName);
      setService(found || null);
    } catch (err) {
      console.error('Failed to load service:', err);
    } finally {
      setLoadingService(false);
    }
  };

  const loadDeprecations = async () => {
    try {
      setLoadingDeprecations(true);
      const items = await getDeprecations({ service: serviceName });
      setDeprecations(items);
    } catch (err) {
      console.error('Failed to load deprecations:', err);
    } finally {
      setLoadingDeprecations(false);
    }
  };

  if (loadingService) {
    return (
      <Container>
        <Box textAlign="center" padding="xxl">
          <Spinner size="large" />
          <Box padding={{ top: 's' }}>Loading service...</Box>
        </Box>
      </Container>
    );
  }

  if (!service) {
    return (
      <Container>
        <Box textAlign="center" padding="xxl">
          <Box variant="h2">Service not found</Box>
          <Box padding={{ top: 's' }}>
            The service "{serviceName}" does not exist in the configuration.
          </Box>
          <Box padding={{ top: 'm' }}>
            <Button onClick={() => navigate('/services')}>Back to services</Button>
          </Box>
        </Box>
      </Container>
    );
  }

  return (
    <SpaceBetween size="l">
      {/* Header with back navigation */}
      <Container
        header={
          <Header
            variant="h1"
            actions={
              <Button variant="normal" iconName="arrow-left" onClick={() => navigate('/services')}>
                Back to services
              </Button>
            }
            description={`Extraction status and catalog entries for ${service.name}`}
          >
            {service.name}
          </Header>
        }
      >
        <ColumnLayout columns={4} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Identifier</Box>
            <Box>{service.service_name}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Status</Box>
            <Box>
              {service.enabled ? (
                <StatusIndicator type="success">Enabled</StatusIndicator>
              ) : (
                <StatusIndicator type="stopped">Disabled</StatusIndicator>
              )}
            </Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Last extraction</Box>
            <Box>
              {service.last_extraction
                ? new Date(service.last_extraction).toLocaleString()
                : 'Never'}
            </Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Extraction count</Box>
            <Box>{service.extraction_count || 0}</Box>
          </div>
        </ColumnLayout>
      </Container>

      {/* Lifecycle / Deprecation Items Section */}
      <ExpandableSection
        variant="container"
        defaultExpanded={true}
        headerText={`Lifecycle data (${deprecations.length} item${deprecations.length !== 1 ? 's' : ''})`}
        headerDescription="Deprecation and end-of-support items for this service"
      >
        {loadingDeprecations ? (
          <Box textAlign="center" padding="l">
            <Spinner /> Loading lifecycle data...
          </Box>
        ) : deprecations.length === 0 ? (
          <Box textAlign="center" padding="l" color="text-body-secondary">
            No lifecycle data extracted for this service.
          </Box>
        ) : (
          <Table
            columnDefinitions={[
              {
                id: 'name',
                header: 'Name',
                cell: (item) => (
                  <SpaceBetween size="xxxs">
                    <Box variant="strong">
                      {item.service_specific?.name || item.item_id}
                    </Box>
                    {item.service_specific?.identifier && (
                      <Box variant="small" color="text-body-secondary">
                        {item.service_specific.identifier}
                      </Box>
                    )}
                  </SpaceBetween>
                ),
              },
              {
                id: 'status',
                header: 'Status',
                cell: (item) => (
                  <StatusIndicator type={statusMeta(item.status).indicator}>{statusMeta(item.status).label}</StatusIndicator>
                ),
                width: 160,
              },
              {
                id: 'dates',
                header: 'Key dates',
                cell: (item) => {
                  const dateFields = [
                    'deprecation_date',
                    'end_of_support_date',
                    'end_of_standard_support_date',
                    'end_of_extended_support_date',
                    'retirement_date',
                  ];
                  const dates = dateFields
                    .filter((f) => item.service_specific?.[f])
                    .map((f) => (
                      <Box key={f} variant="small">
                        {f.replace(/_/g, ' ')}: {item.service_specific[f]}
                      </Box>
                    ));
                  return dates.length > 0 ? (
                    <SpaceBetween size="xxxs">{dates}</SpaceBetween>
                  ) : (
                    <Box variant="small" color="text-body-secondary">-</Box>
                  );
                },
              },
              {
                id: 'last_verified',
                header: 'Last verified',
                cell: (item) => (
                  <Box variant="small">
                    {new Date(item.last_verified).toLocaleDateString()}
                  </Box>
                ),
                width: 140,
              },
            ]}
            items={deprecations}
            variant="embedded"
            empty={
              <Box textAlign="center" color="inherit" padding="s">
                No lifecycle data
              </Box>
            }
          />
        )}
      </ExpandableSection>
    </SpaceBetween>
  );
}
