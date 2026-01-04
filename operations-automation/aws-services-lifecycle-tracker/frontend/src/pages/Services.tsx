import { useState, useEffect, useRef } from 'react';
import Table from '@cloudscape-design/components/table';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Button from '@cloudscape-design/components/button';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Box from '@cloudscape-design/components/box';
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar';
import Toggle from '@cloudscape-design/components/toggle';
import { getServices, triggerExtraction, updateServiceConfig, getDashboardMetrics, ServiceConfig, DashboardMetrics } from '../api';

export default function Services() {
  const [services, setServices] = useState<ServiceConfig[]>([]);
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [flashbarItems, setFlashbarItems] = useState<FlashbarProps.MessageDefinition[]>([]);
  const [extractingService, setExtractingService] = useState<string | null>(null);
  
  // Polling state for individual service extractions
  const pollingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const isPollingRef = useRef(false);

  useEffect(() => {
    loadData();
    
    // Cleanup polling on unmount
    return () => {
      stopPolling();
    };
  }, []);

  const loadData = async () => {
    await Promise.all([
      loadServices(),
      loadMetrics()
    ]);
  };

  const loadMetrics = async () => {
    try {
      const data = await getDashboardMetrics();
      setMetrics(data);
    } catch (err: any) {
      console.error('Failed to load metrics:', err);
      // Don't show error for metrics as it's supplementary data
    }
  };

  const loadServices = async (showLoading = true) => {
    try {
      if (showLoading) setLoading(true);
      const data = await getServices();
      setServices(data);
    } catch (err: any) {
      setFlashbarItems([{
        type: 'error',
        dismissible: true,
        dismissLabel: 'Dismiss',
        onDismiss: () => setFlashbarItems([]),
        content: `Failed to load services: ${err.message}`,
        id: `load-error-${Date.now()}`
      }]);
    } finally {
      if (showLoading) setLoading(false);
    }
  };

  const startPolling = (serviceName: string) => {
    if (isPollingRef.current) return; // Already polling
    
    isPollingRef.current = true;
    let pollCount = 0;
    const maxPolls = 12; // 12 polls * 5 seconds = 60 seconds max (individual services are faster)
    
    const poll = async () => {
      pollCount++;
      console.log(`Polling services attempt ${pollCount}/${maxPolls} for ${serviceName}`);
      
      try {
        await loadServices(false); // Don't show loading spinner during polling
        await loadMetrics(); // Also refresh metrics during polling
      } catch (error) {
        console.error('Error during services polling:', error);
      }
      
      if (pollCount < maxPolls) {
        pollingIntervalRef.current = setTimeout(poll, 5000); // Poll every 5 seconds
      } else {
        // Polling complete
        stopPolling();
      }
    };
    
    // Start first poll after 2 seconds
    pollingIntervalRef.current = setTimeout(poll, 2000);
  };
  
  const stopPolling = () => {
    if (pollingIntervalRef.current) {
      clearTimeout(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
    isPollingRef.current = false;
  };

  const handleExtract = async (serviceName: string) => {
    try {
      setExtractingService(serviceName);
      
      // Show initial notification
      setFlashbarItems([{
        type: 'info',
        dismissible: true,
        dismissLabel: 'Dismiss',
        onDismiss: () => setFlashbarItems([]),
        content: `Starting extraction for ${serviceName}. This will take about 10-20 seconds. Service data will update in real-time`,
        id: `extract-start-${serviceName}-${Date.now()}`
      }]);
      
      // Start the extraction
      const extractionPromise = triggerExtraction(serviceName);
      
      // Start polling to show real-time updates
      startPolling(serviceName);
      
      // Wait for extraction to complete
      await extractionPromise;
      
      console.log(`Extraction completed successfully for ${serviceName}`);
      
      // Show completion message
      setFlashbarItems(prev => [{
        type: 'success',
        dismissible: true,
        dismissLabel: 'Dismiss',
        onDismiss: () => setFlashbarItems(prev => prev.filter(item => item.id !== `extract-complete-${serviceName}`)),
        content: `${serviceName} extraction completed successfully!`,
        id: `extract-complete-${serviceName}`
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
      setExtractingService(null);
    }
  };

  const handleToggleEnabled = async (serviceName: string, enabled: boolean) => {
    try {
      await updateServiceConfig(serviceName, { enabled });
      await loadServices();
      setFlashbarItems([{
        type: 'success',
        dismissible: true,
        dismissLabel: 'Dismiss',
        onDismiss: () => setFlashbarItems([]),
        content: `${serviceName} ${enabled ? 'enabled' : 'disabled'} successfully.`,
        id: `toggle-${serviceName}-${Date.now()}`
      }]);
    } catch (err: any) {
      setFlashbarItems([{
        type: 'error',
        dismissible: true,
        dismissLabel: 'Dismiss',
        onDismiss: () => setFlashbarItems([]),
        content: `Failed to update service: ${err.message}`,
        id: `error-${Date.now()}`
      }]);
    }
  };

  return (
    <SpaceBetween size="l">
      <Flashbar items={flashbarItems} stackItems />

      <Table
        columnDefinitions={[
          {
            id: 'name',
            header: 'Service',
            cell: (item) => (
              <SpaceBetween size="xxxs">
                <Box variant="strong">{item.name}</Box>
                <Box variant="small" color="text-body-secondary">
                  {item.service_name}
                </Box>
              </SpaceBetween>
            ),
            sortingField: 'name',
          },
          {
            id: 'enabled',
            header: 'Status',
            cell: (item) => (
              <Toggle
                checked={item.enabled}
                onChange={({ detail }) => handleToggleEnabled(item.service_name, detail.checked)}
              />
            ),
          },
          {
            id: 'last_extraction',
            header: 'Last Extraction',
            cell: (item) => (
              item.last_extraction ? (
                <Box>
                  {new Date(item.last_extraction).toLocaleString()}
                </Box>
              ) : (
                <Box color="text-body-secondary">Never</Box>
              )
            ),
          },
          {
            id: 'extraction_count',
            header: 'Extractions',
            cell: (item) => item.extraction_count || 0,
          },
          {
            id: 'item_count',
            header: 'Items',
            cell: (item) => {
              const count = metrics?.by_service?.[item.service_name] || 0;
              return (
                <Box>
                  {count}
                </Box>
              );
            },
          },
          {
            id: 'last_extraction_duration',
            header: 'Duration',
            cell: (item) => {
              const duration = item.last_extraction_duration;
              
              if (duration === undefined || duration === null) {
                return <Box color="text-body-secondary">-</Box>;
              }
              
              return (
                <Box>
                  {duration}s
                </Box>
              );
            },
          },
          {
            id: 'success_rate',
            header: 'Success Rate',
            cell: (item) => {
              const rate = item.success_rate;
              
              // If no success rate data, show empty
              if (rate === undefined || rate === null) {
                return '';
              }
              
              return (
                <StatusIndicator type={rate >= 80 ? 'success' : rate >= 50 ? 'warning' : 'error'}>
                  {rate.toFixed(1)}%
                </StatusIndicator>
              );
            },
          },
          {
            id: 'last_refresh_origin',
            header: 'Origin',
            cell: (item) => {
              const origin = item.last_refresh_origin;
              
              // If no origin data, show empty
              if (!origin) {
                return <Box color="text-body-secondary">-</Box>;
              }
              
              const originInfo = {
                'Auto': { text: 'Scheduled', type: 'info' as const, icon: 'calendar' },
                'manual': { text: 'Manual', type: 'success' as const, icon: 'user-profile' },
                'scheduler': { text: 'Scheduled', type: 'info' as const, icon: 'calendar' }
              };
              const info = originInfo[origin as keyof typeof originInfo];
              
              // If unknown origin type, show the raw value
              if (!info) {
                return <Box variant="small">{origin}</Box>;
              }
              
              return (
                <StatusIndicator type={info.type} iconAriaLabel={info.icon}>
                  {info.text}
                </StatusIndicator>
              );
            },
          },
          {
            id: 'actions',
            header: 'Actions',
            cell: (item) => (
              <Button
                variant="normal"
                iconName="refresh"
                loading={extractingService === item.service_name}
                disabled={!item.enabled || extractingService !== null}
                onClick={() => handleExtract(item.service_name)}
              />
            ),
          },
        ]}
        items={services}
        loading={loading}
        loadingText="Loading services..."
        empty={
          <Box textAlign="center" color="inherit">
            <Box padding={{ bottom: 's' }} variant="p" color="inherit">
              No services configured
            </Box>
          </Box>
        }
        header={
          <Header
            variant="h1"
            counter={`(${services.length})`}
            description="Manage AWS services monitored for deprecation information"
          >
            Services
          </Header>
        }
      />
    </SpaceBetween>
  );
}
