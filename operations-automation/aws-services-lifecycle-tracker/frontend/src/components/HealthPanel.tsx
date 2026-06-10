import { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Badge from '@cloudscape-design/components/badge';
import Button from '@cloudscape-design/components/button';
import { fetchHealthSummary, HealthSummary, HealthEvent } from '../api';

/**
 * Maps a health event severity to a Cloudscape StatusIndicator type.
 * - critical → error (red)
 * - high/warning → warning (orange)
 * - medium/info → info (blue)
 * - low → success (green)
 */
function getSeverityIndicatorType(severity: string): 'error' | 'warning' | 'info' | 'success' {
  switch (severity) {
    case 'critical':
      return 'error';
    case 'high':
    case 'warning':
      return 'warning';
    case 'medium':
    case 'info':
      return 'info';
    case 'low':
      return 'success';
    default:
      return 'info';
  }
}

/**
 * Maps a severity to a human-readable label.
 */
function getSeverityLabel(severity: string): string {
  switch (severity) {
    case 'critical':
      return 'Critique';
    case 'high':
    case 'warning':
      return 'Avertissement';
    case 'medium':
    case 'info':
      return 'Informatif';
    case 'low':
      return 'Faible';
    default:
      return severity;
  }
}

/**
 * Maps event_type_category to a readable label.
 */
function getCategoryLabel(category: string): string {
  switch (category) {
    case 'issue':
      return 'Incident';
    case 'scheduledChange':
      return 'Maintenance planifiée';
    case 'accountNotification':
      return 'Notification';
    default:
      return category;
  }
}

/**
 * Maps a Badge color based on severity.
 */
function getBadgeColor(severity: string): 'red' | 'blue' | 'green' | 'grey' {
  switch (severity) {
    case 'critical':
    case 'high':
    case 'warning':
      return 'red';
    case 'medium':
    case 'info':
      return 'blue';
    case 'low':
      return 'green';
    default:
      return 'grey';
  }
}

export default function HealthPanel() {
  const [healthData, setHealthData] = useState<HealthSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadHealthData();
  }, []);

  const loadHealthData = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await fetchHealthSummary();
      setHealthData(data);
    } catch (err: any) {
      setError(err.message || 'Erreur lors du chargement des données Health');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <Container
        header={<Header variant="h2">AWS Health</Header>}
      >
        <Box textAlign="center" padding="l">
          <StatusIndicator type="loading">Chargement des événements Health...</StatusIndicator>
        </Box>
      </Container>
    );
  }

  if (error) {
    return (
      <Container
        header={
          <Header
            variant="h2"
            actions={
              <Button iconName="refresh" onClick={loadHealthData}>
                Actualiser
              </Button>
            }
          >
            AWS Health
          </Header>
        }
      >
        <Box textAlign="center" padding="l">
          <StatusIndicator type="error">{error}</StatusIndicator>
        </Box>
      </Container>
    );
  }

  const hasActiveEvents = healthData && healthData.total_active > 0;

  return (
    <Container
      header={
        <Header
          variant="h2"
          counter={hasActiveEvents ? `(${healthData.total_active})` : undefined}
          actions={
            <Button iconName="refresh" onClick={loadHealthData} variant="icon" />
          }
        >
          AWS Health
        </Header>
      }
    >
      {!hasActiveEvents ? (
        <Box textAlign="center" padding="l">
          <SpaceBetween size="xs" alignItems="center">
            <StatusIndicator type="success">
              Statut opérationnel normal
            </StatusIndicator>
            <Box variant="small" color="text-body-secondary">
              Aucun événement actif détecté sur vos services
            </Box>
          </SpaceBetween>
        </Box>
      ) : (
        <SpaceBetween size="m">
          {Object.entries(healthData!.by_service).map(([serviceName, events]) => (
            <ExpandableSection
              key={serviceName}
              headerText={serviceName}
              headerCounter={`(${events.length})`}
              defaultExpanded={events.some(e => e.severity === 'critical' || e.severity === 'high')}
            >
              <SpaceBetween size="s">
                {events.map((event: HealthEvent) => (
                  <div
                    key={event.event_arn}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'flex-start',
                      padding: '8px 0',
                      borderBottom: '1px solid #e9ebed'
                    }}
                  >
                    <SpaceBetween size="xxs">
                      <Box>
                        <StatusIndicator type={getSeverityIndicatorType(event.severity)}>
                          {event.event_type_code || event.description?.substring(0, 80) || 'Événement Health'}
                        </StatusIndicator>
                      </Box>
                      <Box variant="small" color="text-body-secondary">
                        {getCategoryLabel(event.event_type_category)}
                        {event.region && ` · ${event.region}`}
                        {event.start_time && ` · ${new Date(event.start_time).toLocaleDateString()}`}
                      </Box>
                      {event.description && (
                        <Box variant="small">
                          {event.description.length > 150
                            ? event.description.substring(0, 150) + '...'
                            : event.description}
                        </Box>
                      )}
                    </SpaceBetween>
                    <Badge color={getBadgeColor(event.severity)}>
                      {getSeverityLabel(event.severity)}
                    </Badge>
                  </div>
                ))}
              </SpaceBetween>
            </ExpandableSection>
          ))}
        </SpaceBetween>
      )}
    </Container>
  );
}
