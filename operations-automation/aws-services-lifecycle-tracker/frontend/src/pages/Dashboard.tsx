import { useState, useEffect, useRef } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Grid from '@cloudscape-design/components/grid';
import Box from '@cloudscape-design/components/box';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Button from '@cloudscape-design/components/button';
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar';
import Popover from '@cloudscape-design/components/popover';
import { getDashboardMetrics, startRefreshAll, getRefreshStatus, DashboardMetrics, RefreshProgress } from '../api';
import HealthPanel from '../components/HealthPanel';

// sessionStorage key for the in-flight refresh execution ARN. The pipeline
// runs server-side as a Lambda durable execution; this only lets the UI
// re-attach to it after a page navigation or reload.
const REFRESH_ARN_KEY = 'lifecycle-refresh-execution-arn';

// Services covered by the account scan phase
const DISCOVERY_SERVICES = [
  'Lambda (runtimes)',
  'RDS (engine versions)',
  'EKS (Kubernetes versions)',
  'ElastiCache (Redis/Memcached)',
  'OpenSearch (engine versions)',
  'MSK (Kafka versions)',
  'DocumentDB (MongoDB compatibility)',
  'Neptune (graph DB versions)',
  'Glue (ETL job versions)',
  'Elastic Beanstalk (platforms)',
  'EC2 (older instance families)'
];

export default function Dashboard() {
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [flashbarItems, setFlashbarItems] = useState<FlashbarProps.MessageDefinition[]>([]);
  const [extracting, setExtracting] = useState(false);
  const [progress, setProgress] = useState<RefreshProgress | null>(null);
  
  // Polling state
  const pollingIntervalRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    loadMetrics();

    // Re-attach to an in-flight Refresh All (e.g. after navigating away and
    // back). The execution itself runs server-side and is unaffected.
    const inFlightArn = sessionStorage.getItem(REFRESH_ARN_KEY);
    if (inFlightArn) {
      setExtracting(true);
      pollExecution(inFlightArn);
    }

    // Cleanup polling on unmount (stops observing only - never the batch)
    return () => {
      if (pollingIntervalRef.current) {
        clearTimeout(pollingIntervalRef.current);
        pollingIntervalRef.current = null;
      }
    };
  }, []);

  const loadMetrics = async (showLoading = true) => {
    try {
      if (showLoading) setLoading(true);
      const data = await getDashboardMetrics();
      setMetrics(data);
    } catch (err: any) {
      setFlashbarItems([{
        type: 'error',
        dismissible: true,
        dismissLabel: 'Dismiss',
        onDismiss: () => setFlashbarItems([]),
        content: `Failed to load metrics: ${err.message}`,
        id: `error-${Date.now()}`
      }]);
    } finally {
      if (showLoading) setLoading(false);
    }
  };

  // Observe a server-side Refresh All execution until it reaches a terminal
  // state. Polling is passive: stopping it (unmount) never affects the batch.
  const pollExecution = async (executionArn: string, consecutiveErrors = 0) => {
    try {
      const execution = await getRefreshStatus(executionArn);

      if (execution.status === 'RUNNING') {
        setProgress(execution.progress || null);
        loadMetrics(false); // refresh counts while the pipeline progresses
        pollingIntervalRef.current = setTimeout(() => pollExecution(executionArn), 5000);
        return;
      }

      // Terminal state reached
      sessionStorage.removeItem(REFRESH_ARN_KEY);
      setExtracting(false);
      setProgress(null);
      await loadMetrics(false);

      if (execution.status === 'SUCCEEDED') {
        const summary = execution.summary;
        const ex = summary?.extract;
        const sc = summary?.scan;
        const failures = [...(ex?.failed || []), ...(sc?.failed_cells || [])];
        const detail = summary
          ? `Facts: ${ex!.succeeded}/${ex!.total} services (${ex!.items_extracted} items). ` +
            `Inventory: ${sc!.items_discovered} assets scanned, ${sc!.needs_attention} need attention.`
          : '';
        if (failures.length > 0) {
          setFlashbarItems([{
            type: 'warning',
            dismissible: true,
            dismissLabel: 'Dismiss',
            onDismiss: () => setFlashbarItems([]),
            content: `Refresh finished with failures. ${detail} Failed: ${failures.join(', ')}`,
            id: `refresh-partial-${Date.now()}`
          }]);
        } else {
          setFlashbarItems([{
            type: 'success',
            dismissible: true,
            dismissLabel: 'Dismiss',
            onDismiss: () => setFlashbarItems([]),
            content: detail ? `Refresh complete. ${detail}` : 'Refresh completed successfully.',
            id: `refresh-success-${Date.now()}`
          }]);
        }
      } else {
        setFlashbarItems([{
          type: 'error',
          dismissible: true,
          dismissLabel: 'Dismiss',
          onDismiss: () => setFlashbarItems([]),
          content: `Refresh ended with status ${execution.status}. Check the pipeline function's durable executions in the Lambda console for details.`,
          id: `refresh-failed-${Date.now()}`
        }]);
      }
    } catch (error: any) {
      // Transient describe failure - keep observing (up to 3 in a row)
      console.error('Error polling refresh execution:', error);
      if (consecutiveErrors < 3) {
        pollingIntervalRef.current = setTimeout(() => pollExecution(executionArn, consecutiveErrors + 1), 5000);
      } else {
        setExtracting(false);
        setFlashbarItems([{
          type: 'warning',
          dismissible: true,
          dismissLabel: 'Dismiss',
          onDismiss: () => setFlashbarItems([]),
          content: 'Lost track of the refresh progress, but the batch keeps running server-side. Reload the page to re-attach.',
          id: `refresh-poll-error-${Date.now()}`
        }]);
      }
    }
  };

  const handleRefresh = async () => {
    try {
      setExtracting(true);

      // Fire-and-forget: start (or adopt) the server-side batch
      const { executionArn, alreadyRunning } = await startRefreshAll();
      sessionStorage.setItem(REFRESH_ARN_KEY, executionArn);

      setFlashbarItems([{
        type: 'info',
        dismissible: true,
        dismissLabel: 'Dismiss',
        onDismiss: () => setFlashbarItems([]),
        content: alreadyRunning
          ? 'A refresh is already in progress - showing its status.'
          : 'Refresh started: extracting facts for all enabled services, then scanning the account. It runs server-side, so you can navigate away - progress resumes when you return.',
        id: `extract-all-${Date.now()}`
      }]);

      pollExecution(executionArn);
    } catch (err: any) {
      setExtracting(false);
      setFlashbarItems([{
        type: 'error',
        dismissible: true,
        dismissLabel: 'Dismiss',
        onDismiss: () => setFlashbarItems([]),
        content: `Failed to start refresh: ${err.message}`,
        id: `error-${Date.now()}`
      }]);
    }
  };

  if (loading) {
    return (
      <Container>
        <Box textAlign="center" padding="xxl">
          <StatusIndicator type="loading">Loading dashboard...</StatusIndicator>
        </Box>
      </Container>
    );
  }

  return (
    <SpaceBetween size="l">
      <Flashbar items={flashbarItems} stackItems />

      <Container
        header={
          <Header
            variant="h1"
            actions={
              <SpaceBetween direction="horizontal" size="xxs">
                <Button
                  variant="primary"
                  iconName="refresh"
                  loading={extracting}
                  onClick={handleRefresh}
                  disabled={extracting}
                >
                  {extracting
                    ? progress
                      ? `Refreshing... (${progress.extract_done} extracted, ${progress.scan_done} scanned)`
                      : 'Refreshing...'
                    : 'Refresh'}
                </Button>
                <Popover
                  dismissButton={false}
                  position="bottom"
                  size="medium"
                  triggerType="text"
                  content={
                    <SpaceBetween size="xs">
                      <Box variant="strong">One end-to-end run (Lambda durable function):</Box>
                      <Box variant="small">
                        1. Extracts deprecation facts from the AWS documentation for every enabled service.
                      </Box>
                      <Box variant="small">
                        2. Scans this account for resources on deprecated versions:
                        {DISCOVERY_SERVICES.map((service, i) => (
                          <div key={i}>• {service}</div>
                        ))}
                      </Box>
                      <Box variant="small">
                        3. Reconciles the inventory and publishes a summary to SNS.
                      </Box>
                      <Box variant="small" color="text-body-secondary">
                        The run continues server-side if you navigate away. To add services: edit
                        <code> scripts/service_configs.json</code> (facts) or <code>agent/account_discovery.py</code>
                        plus the IAM grants in <code>cdk/lib/pipeline-stack.ts</code> (scan).
                      </Box>
                    </SpaceBetween>
                  }
                >
                  <Box color="text-status-info" display="inline">ⓘ</Box>
                </Popover>
              </SpaceBetween>
            }
          >
            AWS Services Lifecycle Tracker
          </Header>
        }
      >
        <SpaceBetween size="l">
          <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
            <Container>
              <Box variant="awsui-key-label">Total Services</Box>
              <Box variant="h1" fontSize="display-l" fontWeight="bold">
                {metrics?.total_services || 0}
              </Box>
              <Box variant="small" color="text-status-info">
                {metrics?.enabled_services || 0} enabled
              </Box>
            </Container>

            <Container>
              <Box variant="awsui-key-label">Total Items</Box>
              <Box variant="h1" fontSize="display-l" fontWeight="bold">
                {metrics?.total_items || 0}
              </Box>
              <Box variant="small" color="text-body-secondary">
                Deprecation items tracked
              </Box>
            </Container>

            <Container>
              <Box variant="awsui-key-label">Deprecated</Box>
              <Box variant="h1" fontSize="display-l" fontWeight="bold" color="text-status-warning">
                {metrics?.by_status.deprecated || 0}
              </Box>
              <Box variant="small" color="text-body-secondary">
                Plan migration
              </Box>
            </Container>

            <Container>
              <Box variant="awsui-key-label">End of Life</Box>
              <Box variant="h1" fontSize="display-l" fontWeight="bold" color="text-status-error">
                {metrics?.by_status.end_of_life || 0}
              </Box>
              <Box variant="small" color="text-body-secondary">
                Immediate action required
              </Box>
            </Container>
          </Grid>

          <HealthPanel />

          <Container header={<Header variant="h2">Status Breakdown</Header>}>
            <SpaceBetween size="m">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Box>
                  <StatusIndicator type="warning">Deprecated</StatusIndicator>
                </Box>
                <Box variant="h3">{metrics?.by_status.deprecated || 0}</Box>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Box>
                  <StatusIndicator type="info">Extended Support</StatusIndicator>
                </Box>
                <Box variant="h3">{metrics?.by_status.extended_support || 0}</Box>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Box>
                  <StatusIndicator type="error">End of Life</StatusIndicator>
                </Box>
                <Box variant="h3">{metrics?.by_status.end_of_life || 0}</Box>
              </div>
            </SpaceBetween>
          </Container>

          <Container header={<Header variant="h2">Recent Extractions</Header>}>
            {metrics?.recent_extractions && metrics.recent_extractions.length > 0 ? (
              <SpaceBetween size="s">
                {metrics.recent_extractions.map((extraction, index) => (
                  <div key={index} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Box>
                      <strong>{extraction.service_name}</strong>
                      <Box variant="small" color="text-body-secondary">
                        {new Date(extraction.timestamp).toLocaleString()}
                      </Box>
                    </Box>
                    <StatusIndicator type={extraction.success ? 'success' : 'error'}>
                      {extraction.success ? 'Success' : 'Failed'}
                    </StatusIndicator>
                  </div>
                ))}
              </SpaceBetween>
            ) : (
              <Box textAlign="center" color="text-body-secondary" padding="l">
                No recent extractions
              </Box>
            )}
          </Container>
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
}
