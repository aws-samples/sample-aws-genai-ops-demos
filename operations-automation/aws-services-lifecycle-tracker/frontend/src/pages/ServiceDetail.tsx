import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Badge from '@cloudscape-design/components/badge';
import Button from '@cloudscape-design/components/button';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Table from '@cloudscape-design/components/table';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Spinner from '@cloudscape-design/components/spinner';
import {
  getServices,
  getDeprecations,
  fetchHealthEvents,
  ServiceConfig,
  DeprecationItem,
  HealthEvent,
} from '../api';

function getSeverityIndicator(severity: string) {
  switch (severity) {
    case 'critical':
      return <StatusIndicator type="error">Critical</StatusIndicator>;
    case 'high':
      return <StatusIndicator type="warning">High</StatusIndicator>;
    case 'medium':
      return <StatusIndicator type="info">Medium</StatusIndicator>;
    case 'low':
      return <StatusIndicator type="success">Low</StatusIndicator>;
    default:
      return <StatusIndicator>{severity}</StatusIndicator>;
  }
}

function getCategoryBadge(category: string) {
  switch (category) {
    case 'issue':
      return <Badge color="red">Incident</Badge>;
    case 'scheduledChange':
      return <Badge color="blue">Scheduled maintenance</Badge>;
    case 'accountNotification':
      return <Badge color="grey">Notification</Badge>;
    default:
      return <Badge>{category}</Badge>;
  }
}

function getStatusBadge(statusCode: string) {
  switch (statusCode) {
    case 'open':
      return <StatusIndicator type="error">Open</StatusIndicator>;
    case 'upcoming':
      return <StatusIndicator type="warning">Upcoming</StatusIndicator>;
    case 'closed':
      return <StatusIndicator type="success">Resolved</StatusIndicator>;
    default:
      return <StatusIndicator>{statusCode}</StatusIndicator>;
  }
}

export default function ServiceDetail() {
  const { serviceName } = useParams<{ serviceName: string }>();
  const navigate = useNavigate();

  const [service, setService] = useState<ServiceConfig | null>(null);
  const [deprecations, setDeprecations] = useState<DeprecationItem[]>([]);
  const [healthEvents, setHealthEvents] = useState<HealthEvent[]>([]);
  const [loadingService, setLoadingService] = useState(true);
  const [loadingDeprecations, setLoadingDeprecations] = useState(true);
  const [loadingHealth, setLoadingHealth] = useState(true);

  useEffect(() => {
    if (serviceName) {
      loadServiceData();
    }
  }, [serviceName]);

  const loadServiceData = async () => {
    await Promise.all([
      loadService(),
      loadDeprecations(),
      loadHealthEvents(),
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

  const loadHealthEvents = async () => {
    try {
      setLoadingHealth(true);
      const events = await fetchHealthEvents({ service: serviceName });
      setHealthEvents(events);
    } catch (err) {
      console.error('Failed to load health events:', err);
    } finally {
      setLoadingHealth(false);
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

  const activeHealthEvents = healthEvents.filter(
    (e) => e.status_code === 'open' || e.status_code === 'upcoming'
  );

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
            description={`Details and Health events for ${service.name}`}
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

      {/* Health Events Section */}
      <ExpandableSection
        variant="container"
        defaultExpanded={true}
        headerText={`AWS Health events (${activeHealthEvents.length} active)`}
        headerDescription="Incidents, scheduled maintenance, and notifications affecting this service"
      >
        {loadingHealth ? (
          <Box textAlign="center" padding="l">
            <Spinner /> Loading Health events...
          </Box>
        ) : activeHealthEvents.length === 0 ? (
          <Box textAlign="center" padding="l">
            <StatusIndicator type="success">
              No active Health events — normal operational status
            </StatusIndicator>
          </Box>
        ) : (
          <Table
            columnDefinitions={[
              {
                id: 'severity',
                header: 'Severity',
                cell: (item) => getSeverityIndicator(item.severity),
                width: 120,
              },
              {
                id: 'category',
                header: 'Type',
                cell: (item) => getCategoryBadge(item.event_type_category),
                width: 160,
              },
              {
                id: 'status',
                header: 'Status',
                cell: (item) => getStatusBadge(item.status_code),
                width: 110,
              },
              {
                id: 'description',
                header: 'Description',
                cell: (item) => (
                  <SpaceBetween size="xxxs">
                    <Box variant="strong">{item.event_type_code}</Box>
                    <Box variant="small" color="text-body-secondary">
                      {item.description.length > 150
                        ? `${item.description.substring(0, 150)}...`
                        : item.description}
                    </Box>
                  </SpaceBetween>
                ),
              },
              {
                id: 'region',
                header: 'Region',
                cell: (item) => item.region || '-',
                width: 130,
              },
              {
                id: 'start_time',
                header: 'Start',
                cell: (item) =>
                  item.start_time
                    ? new Date(item.start_time).toLocaleString()
                    : '-',
                width: 170,
              },
            ]}
            items={activeHealthEvents}
            variant="embedded"
            empty={
              <Box textAlign="center" color="inherit" padding="s">
                No active Health events
              </Box>
            }
          />
        )}
      </ExpandableSection>

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
                cell: (item) => {
                  switch (item.status) {
                    case 'deprecated':
                      return <StatusIndicator type="warning">Deprecated</StatusIndicator>;
                    case 'extended_support':
                      return <StatusIndicator type="info">Extended Support</StatusIndicator>;
                    case 'end_of_life':
                      return <StatusIndicator type="error">End of Life</StatusIndicator>;
                    default:
                      return <StatusIndicator>{item.status}</StatusIndicator>;
                  }
                },
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
