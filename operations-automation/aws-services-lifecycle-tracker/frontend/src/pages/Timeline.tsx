import { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import Alert from '@cloudscape-design/components/alert';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Badge from '@cloudscape-design/components/badge';
import Select from '@cloudscape-design/components/select';
import { getDeprecations, DeprecationItem } from '../api';

export default function Timeline() {
  const [items, setItems] = useState<DeprecationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [milestoneFilter, setMilestoneFilter] = useState<any>({ value: 'all' });

  useEffect(() => {
    loadDeprecations();
  }, []);

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

  const getUpcomingItems = () => {
    const now = new Date();
    const upcoming = items.filter(item => {
      // Get all upcoming dates for this item
      const upcomingDates = getUpcomingDates(item);
      
      // If no filter, show items with any upcoming date
      if (milestoneFilter.value === 'all') {
        return upcomingDates.length > 0;
      }
      
      // Filter by specific milestone type
      return upcomingDates.some(d => {
        if (milestoneFilter.value === 'deprecation') return d.label === 'Deprecation';
        if (milestoneFilter.value === 'end_of_support') return d.label === 'End of Support';
        if (milestoneFilter.value === 'end_of_standard_support') return d.label === 'End of Standard Support';
        if (milestoneFilter.value === 'end_of_extended_support') return d.label === 'End of Extended Support';
        return false;
      });
    });
    
    return upcoming.sort((a, b) => {
      const getEarliestDate = (item: DeprecationItem) => {
        const upcomingDates = getUpcomingDates(item);
        if (upcomingDates.length === 0) return Infinity;
        return upcomingDates[0].date.getTime();
      };
      
      return getEarliestDate(a) - getEarliestDate(b);
    });
  };

  const getUpcomingDates = (item: DeprecationItem) => {
    const now = new Date();
    const dateInfo = [];
    
    if (item.service_specific.deprecation_date) {
      const date = new Date(item.service_specific.deprecation_date);
      if (date > now) {
        const daysUntil = Math.ceil((date.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
        dateInfo.push({ label: 'Deprecation', days: daysUntil, date });
      }
    }
    
    if (item.service_specific.end_of_standard_support_date) {
      const date = new Date(item.service_specific.end_of_standard_support_date);
      if (date > now) {
        const daysUntil = Math.ceil((date.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
        dateInfo.push({ label: 'End of Standard Support', days: daysUntil, date });
      }
    }
    
    if (item.service_specific.end_of_support_date) {
      const date = new Date(item.service_specific.end_of_support_date);
      if (date > now) {
        const daysUntil = Math.ceil((date.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
        dateInfo.push({ label: 'End of Support', days: daysUntil, date });
      }
    }
    
    if (item.service_specific.end_of_extended_support_date) {
      const date = new Date(item.service_specific.end_of_extended_support_date);
      if (date > now) {
        const daysUntil = Math.ceil((date.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
        dateInfo.push({ label: 'End of Extended Support', days: daysUntil, date });
      }
    }
    
    // Sort by days ascending (soonest first)
    return dateInfo.sort((a, b) => a.days - b.days);
  };

  const getUrgencyLevel = (days: number) => {
    if (days <= 90) return { level: 'critical', type: 'error' as const };
    if (days <= 180) return { level: 'high', type: 'warning' as const };
    if (days <= 365) return { level: 'medium', type: 'info' as const };
    return { level: 'low', type: 'info' as const };
  };

  if (loading) {
    return (
      <Container>
        <Box textAlign="center" padding="xxl">
          <StatusIndicator type="loading">Loading timeline...</StatusIndicator>
        </Box>
      </Container>
    );
  }

  const upcomingItems = getUpcomingItems();

  return (
    <SpaceBetween size="l">
      {error && (
        <Alert type="error" dismissible onDismiss={() => setError('')}>
          {error}
        </Alert>
      )}

      <Container
        header={
          <Header
            variant="h1"
            counter={`(${upcomingItems.length})`}
            description="Upcoming deprecations and end-of-life dates across all monitored services"
            actions={
              <Select
                selectedOption={milestoneFilter}
                onChange={({ detail }) => setMilestoneFilter(detail.selectedOption)}
                options={[
                  { label: 'All Milestones', value: 'all' },
                  { label: 'Deprecation', value: 'deprecation' },
                  { label: 'End of Standard Support', value: 'end_of_standard_support' },
                  { label: 'End of Support', value: 'end_of_support' },
                  { label: 'End of Extended Support', value: 'end_of_extended_support' },
                ]}
                selectedAriaLabel="Selected milestone filter"
              />
            }
          >
            Timeline
          </Header>
        }
      >
        {upcomingItems.length === 0 ? (
          <Box textAlign="center" color="text-body-secondary" padding="xxl">
            No upcoming deprecations found
          </Box>
        ) : (
          <SpaceBetween size="m">
            {upcomingItems.map((item, index) => {
              const upcomingDates = getUpcomingDates(item);
              const mostUrgent = upcomingDates[0];
              const urgency = mostUrgent ? getUrgencyLevel(mostUrgent.days) : { level: 'low', type: 'info' as const };
              
              return (
                <Container key={index}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div style={{ flex: 1 }}>
                      <SpaceBetween size="xs">
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <Badge color="blue">{item.service_name.toUpperCase()}</Badge>
                          <Box variant="strong">
                            {item.service_specific.name || item.item_id}
                          </Box>
                        </div>
                        <Box variant="small" color="text-body-secondary">
                          {item.service_specific.identifier || ''}
                        </Box>
                        {upcomingDates.length > 0 && (
                          <SpaceBetween size="xxs">
                            {upcomingDates.map((dateInfo, idx) => {
                              const dateUrgency = getUrgencyLevel(dateInfo.days);
                              return (
                                <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                  <StatusIndicator type={dateUrgency.type}>
                                    {dateInfo.days} days until {dateInfo.label}
                                  </StatusIndicator>
                                  <Box variant="small" color="text-body-secondary">
                                    ({dateInfo.date.toLocaleDateString()})
                                  </Box>
                                </div>
                              );
                            })}
                          </SpaceBetween>
                        )}
                      </SpaceBetween>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <StatusIndicator type={item.status === 'end_of_life' ? 'error' : item.status === 'deprecated' ? 'warning' : 'info'}>
                        {item.status.replace('_', ' ').toUpperCase()}
                      </StatusIndicator>
                    </div>
                  </div>
                </Container>
              );
            })}
          </SpaceBetween>
        )}
      </Container>
    </SpaceBetween>
  );
}
