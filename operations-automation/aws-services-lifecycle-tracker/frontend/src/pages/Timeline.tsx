import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import Alert from '@cloudscape-design/components/alert';
import Link from '@cloudscape-design/components/link';
import { getLifecycleData, DeprecationItem } from '../api';
import { statusMeta, serviceLabel, itemName, formatDate, isInventory, resourceCount, resourceWord } from '../lifecycle';

// Dates that mark a deadline (in the order they are listed per item)
const MILESTONES: Array<[string, string]> = [
  ['deprecation_date', 'deprecation'],
  ['end_of_standard_support_date', 'end of standard support'],
  ['end_of_support_date', 'end of support'],
  ['retirement_date', 'retirement'],
  ['target_retirement_date', 'target retirement'],
  ['block_update_date', 'function updates blocked'],
  ['block_create_date', 'function creation blocked'],
  ['end_of_extended_support_date', 'end of extended support'],
];

interface Milestone { item: DeprecationItem; label: string; date: Date; days: number }

const bucketOf = (days: number) =>
  days <= 0 ? 'Passed' : days <= 90 ? 'Next 90 days' : days <= 180 ? '3 to 6 months' : days <= 365 ? '6 to 12 months' : 'Later';
const BUCKETS = ['Passed', 'Next 90 days', '3 to 6 months', '6 to 12 months', 'Later'];
const bucketType = (b: string) => (b === 'Passed' || b === 'Next 90 days' ? 'error' : b === '3 to 6 months' ? 'warning' : 'info') as 'error' | 'warning' | 'info';

export default function Timeline() {
  const navigate = useNavigate();
  const [inventory, setInventory] = useState<DeprecationItem[]>([]);
  const [facts, setFacts] = useState<DeprecationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lens, setLens] = useState<'mine' | 'catalog'>('mine');

  useEffect(() => {
    (async () => {
      try {
        const data = await getLifecycleData();
        setInventory(data.inventory);
        setFacts(data.facts);
      } catch (err: any) {
        setError(`Failed to load timeline: ${err.message}`);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const milestones = useMemo(() => {
    const now = Date.now();
    const source = lens === 'mine' ? inventory : facts;
    const out: Milestone[] = [];
    for (const item of source) {
      for (const [field, label] of MILESTONES) {
        const raw = item.service_specific?.[field];
        if (!raw || typeof raw !== 'string' || /^(n\/a|tbd|-)$/i.test(raw)) continue;
        const d = new Date(raw);
        if (isNaN(d.getTime())) continue;
        const days = Math.ceil((d.getTime() - now) / 86_400_000);
        // Catalog: only upcoming dates (the past is history). Mine: also show
        // what already passed - those are the resources in trouble today.
        if (lens === 'catalog' && days < 0) continue;
        if (lens === 'mine' && days < -365) continue;
        out.push({ item, label, date: d, days });
      }
    }
    return out.sort((a, b) => a.days - b.days);
  }, [inventory, facts, lens]);

  const grouped = useMemo(() => {
    const g = new Map<string, Milestone[]>();
    for (const m of milestones) g.set(bucketOf(m.days), [...(g.get(bucketOf(m.days)) || []), m]);
    return BUCKETS.filter((b) => g.has(b)).map((b) => [b, g.get(b)!] as const);
  }, [milestones]);

  if (loading) {
    return <Container><Box textAlign="center" padding="xxl"><StatusIndicator type="loading">Loading timeline...</StatusIndicator></Box></Container>;
  }

  return (
    <SpaceBetween size="l">
      {error && <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>}

      <Container
        header={
          <Header
            variant="h1"
            counter={`(${milestones.length})`}
            description={lens === 'mine'
              ? 'Deadlines for the versions running in this account, soonest first. Dates that already passed are shown too - those resources are the ones in trouble today.'
              : 'Upcoming deadlines across the whole catalog, whether or not you run the version.'}
            actions={
              <SegmentedControl
                selectedId={lens}
                onChange={({ detail }) => setLens(detail.selectedId as 'mine' | 'catalog')}
                options={[{ text: `My resources (${inventory.length})`, id: 'mine' }, { text: `Catalog (${facts.length})`, id: 'catalog' }]}
              />
            }
          >
            Timeline
          </Header>
        }
      >
        {milestones.length === 0 ? (
          <Box textAlign="center" color="text-body-secondary" padding="xxl">
            {lens === 'mine' ? 'Nothing in your account has an upcoming deadline.' : 'No upcoming deadlines in the catalog.'}
          </Box>
        ) : (
          <SpaceBetween size="l">
            {grouped.map(([bucket, list]) => (
              <SpaceBetween size="s" key={bucket}>
                <Header variant="h3" counter={`(${list.length})`}>
                  <StatusIndicator type={bucketType(bucket)}>{bucket}</StatusIndicator>
                </Header>
                {list.map((m, i) => {
                  const st = statusMeta(m.item.status);
                  const mine = isInventory(m.item);
                  return (
                    <div key={`${m.item.item_id}-${m.label}-${i}`} style={{ display: 'grid', gridTemplateColumns: '110px 1fr auto', gap: '12px', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid #e9ebed' }}>
                      <Box variant="strong">{formatDate(m.date)}</Box>
                      <SpaceBetween size="xxxs">
                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                          <Badge color="blue">{serviceLabel(m.item.service_name)}</Badge>
                          {mine
                            ? <Link onFollow={(e) => { e.preventDefault(); navigate(`/resources?q=${encodeURIComponent(m.item.service_specific?.identifier || '')}&status=all`); }} href="#">{itemName(m.item)}</Link>
                            : <Box variant="strong">{itemName(m.item)}</Box>}
                          <Box variant="small" color="text-body-secondary">{m.label}</Box>
                        </div>
                        {mine && (
                          <Box variant="small">
                            <Link onFollow={(e) => { e.preventDefault(); navigate(`/resources?status=all&details=${encodeURIComponent(m.item.item_id)}`); }} href="#" fontSize="body-s">
                              {resourceCount(m.item)} {resourceWord(resourceCount(m.item))}{m.item.region ? ` in ${m.item.region}` : ''}
                            </Link>
                          </Box>
                        )}
                      </SpaceBetween>
                      <SpaceBetween size="xxxs" alignItems="end">
                        <StatusIndicator type={st.indicator}>{st.label}</StatusIndicator>
                        <Box variant="small" color="text-body-secondary">{m.days < 0 ? `${-m.days} days ago` : m.days === 0 ? 'today' : `in ${m.days} days`}</Box>
                      </SpaceBetween>
                    </div>
                  );
                })}
              </SpaceBetween>
            ))}
          </SpaceBetween>
        )}
      </Container>
    </SpaceBetween>
  );
}
