import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import Alert from '@cloudscape-design/components/alert';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Badge from '@cloudscape-design/components/badge';
import Select from '@cloudscape-design/components/select';
import Toggle from '@cloudscape-design/components/toggle';
import { getDeprecations, DeprecationItem } from '../api';
import { loadRiskMatrix } from '../risk/matrix-loader';
import { assessAll, sortByRisk } from '../risk/engine';
import type { RiskMatrix, RiskAssessment, ActionPlanPrefill } from '../risk/types';
import RiskIndicator from '../components/RiskIndicator';
import ImpactPanel from '../components/ImpactPanel';

export default function Timeline() {
  const navigate = useNavigate();

  const [items, setItems] = useState<DeprecationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [milestoneFilter, setMilestoneFilter] = useState<any>({ value: 'all' });

  // Risk assessment state
  const [riskMatrix, setRiskMatrix] = useState<RiskMatrix | null>(null);
  const [assessments, setAssessments] = useState<Record<string, RiskAssessment>>({});
  const [sortByRiskEnabled, setSortByRiskEnabled] = useState(false);

  // Load risk matrix on component mount
  useEffect(() => {
    loadRiskMatrix().then(setRiskMatrix);
  }, []);

  useEffect(() => {
    loadDeprecations();
  }, []);

  // Recompute assessments when items or matrix change
  useEffect(() => {
    if (riskMatrix && items.length > 0) {
      const results = assessAll(items, riskMatrix);
      // Index by item_id for O(1) lookup
      const assessmentMap: Record<string, RiskAssessment> = {};
      for (const assessment of results) {
        assessmentMap[assessment.itemId] = assessment;
      }
      setAssessments(assessmentMap);
    }
  }, [items, riskMatrix]);

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

  /**
   * Navigate to PlanOfAction page with pre-filled context from risk assessment.
   */
  const handleCreateActionPlan = (prefill: ActionPlanPrefill) => {
    navigate('/plan-of-action', {
      state: {
        prefill: {
          service_name: prefill.service_name,
          item_id: prefill.item_id,
          item_name: prefill.item_name,
          priority: prefill.priority,
          notes: prefill.notes,
        },
      },
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

  const getUpcomingItems = () => {
    const upcoming = items.filter(item => {
      const upcomingDates = getUpcomingDates(item);
      
      if (milestoneFilter.value === 'all') {
        return upcomingDates.length > 0;
      }
      
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

  /**
   * Get items sorted either by timeline (default) or by risk level.
   */
  const getSortedItems = useMemo(() => {
    const upcoming = getUpcomingItems();

    if (!sortByRiskEnabled || Object.keys(assessments).length === 0) {
      return upcoming;
    }

    // Sort by risk: use the sortByRisk utility on assessments, then reorder items accordingly
    const itemAssessments = upcoming
      .map(item => assessments[item.item_id])
      .filter(Boolean);

    const sorted = sortByRisk(itemAssessments);
    const sortedItemIds = sorted.map(a => a.itemId);

    // Reorder items based on sorted risk order
    const itemMap = new Map(upcoming.map(item => [item.item_id, item]));
    const sortedItems: DeprecationItem[] = [];
    for (const id of sortedItemIds) {
      const item = itemMap.get(id);
      if (item) {
        sortedItems.push(item);
        itemMap.delete(id);
      }
    }
    // Append items without assessments at the end
    for (const item of itemMap.values()) {
      sortedItems.push(item);
    }

    return sortedItems;
  }, [items, milestoneFilter, sortByRiskEnabled, assessments]);

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

  const upcomingItems = getSortedItems;

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
              <SpaceBetween direction="horizontal" size="m">
                <Toggle
                  onChange={({ detail }) => setSortByRiskEnabled(detail.checked)}
                  checked={sortByRiskEnabled}
                >
                  Sort by risk
                </Toggle>
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
              </SpaceBetween>
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
              const assessment = assessments[item.item_id] || null;
              
              return (
                <Container key={item.item_id || index}>
                  <SpaceBetween size="s">
                    {/* Main item row */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div style={{ flex: 1 }}>
                        <SpaceBetween size="xs">
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <Badge color="blue">{item.service_name.toUpperCase()}</Badge>
                            <RiskIndicator assessment={assessment} />
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

                    {/* Expandable ImpactPanel below item */}
                    {assessment && (
                      <ImpactPanel
                        assessment={assessment}
                        item={item}
                        onCreateActionPlan={handleCreateActionPlan}
                      />
                    )}
                  </SpaceBetween>
                </Container>
              );
            })}
          </SpaceBetween>
        )}
      </Container>
    </SpaceBetween>
  );
}
