import { useState, useEffect, useRef } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Grid from '@cloudscape-design/components/grid';
import Box from '@cloudscape-design/components/box';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Button from '@cloudscape-design/components/button';
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar';
import { getDashboardMetrics, triggerExtraction, DashboardMetrics } from '../api';

export default function Dashboard() {
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [flashbarItems, setFlashbarItems] = useState<FlashbarProps.MessageDefinition[]>([]);
  const [extracting, setExtracting] = useState(false);
  
  // Polling state
  const pollingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const isPollingRef = useRef(false);

  useEffect(() => {
    loadMetrics();
    
    // Cleanup polling on unmount
    return () => {
      stopPolling();
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

  const startPolling = () => {
    if (isPollingRef.current) return; // Already polling
    
    isPollingRef.current = true;
    let pollCount = 0;
    const maxPolls = 18; // 18 polls * 5 seconds = 90 seconds max
    
    const poll = async () => {
      pollCount++;
      console.log(`Polling attempt ${pollCount}/${maxPolls}`);
      
      try {
        await loadMetrics(false); // Don't show loading spinner during polling
      } catch (error) {
        console.error('Error during polling:', error);
      }
      
      if (pollCount < maxPolls) {
        pollingIntervalRef.current = setTimeout(poll, 5000); // Poll every 5 seconds
      } else {
        // Polling complete
        stopPolling();
      }
    };
    
    // Start first poll after 1 seconds
    pollingIntervalRef.current = setTimeout(poll, 1000);
  };
  
  const stopPolling = () => {
    if (pollingIntervalRef.current) {
      clearTimeout(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
    isPollingRef.current = false;
  };

  const handleExtractAll = async () => {
    try {
      setExtracting(true);
      
      // Show initial notification
      setFlashbarItems([{
        type: 'info',
        dismissible: true,
        dismissLabel: 'Dismiss',
        onDismiss: () => setFlashbarItems([]),
        content: 'Starting extraction for all services. This will take about 60-90 seconds. Metrics will update in real-time',
        id: `extract-all-${Date.now()}`
      }]);
      
      // Start the extraction (this will take ~51 seconds)
      const extractionPromise = triggerExtraction('all');
      
      // Start polling immediately to show progress
      startPolling();
      
      // Wait for extraction to complete
      await extractionPromise;
      
      console.log('Extraction completed successfully');
      
      // Show immediate completion message when extraction finishes
      setFlashbarItems(prev => [{
        type: 'success',
        dismissible: true,
        dismissLabel: 'Dismiss',
        onDismiss: () => setFlashbarItems(prev => prev.filter(item => item.id !== 'extraction-finished')),
        content: 'Extraction completed successfully!',
        id: 'extraction-finished'
      }, ...prev]);
      
    } catch (err: any) {
      stopPolling(); // Stop polling on error
      setFlashbarItems([{
        type: 'error',
        dismissible: true,
        dismissLabel: 'Dismiss',
        onDismiss: () => setFlashbarItems([]),
        content: `Failed to trigger extraction: ${err.message}`,
        id: `error-${Date.now()}`
      }]);
    } finally {
      setExtracting(false);
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
              <Button
                variant="primary"
                iconName="refresh"
                loading={extracting}
                onClick={handleExtractAll}
                disabled={extracting}
              >
                {extracting ? 'Extracting...' : 'Extract All Services'}
              </Button>
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
