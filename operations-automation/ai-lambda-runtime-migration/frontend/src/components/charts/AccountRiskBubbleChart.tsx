// Chart 4 — "Account risk map" (bubble, needs highcharts-more).
//
// Cloudscape v3 has no bubble chart, so this uses Highcharts. One bubble per
// account: x = active deprecated functions in the account, y = urgency (average
// days to deprecation, axis INVERTED so sooner-to-EOL reads higher = more
// urgent), bubble size = count of CRITICAL-priority functions. The account NAME
// is the point label; counts only, never a raw function list.
import { useMemo } from 'react';
import Highcharts from './highcharts-setup';
import Box from '@cloudscape-design/components/box';
import HighchartsReact from './HighchartsReact';
import { MigrationFunction } from '../../api';
import { isActiveBacklog } from '../../migration-tallies';

// Cloudscape data-viz-ish palette (neutral blue for accounts; status colors are
// reserved for the status-colored charts).
const BUBBLE_COLOR = 'rgba(84, 108, 227, 0.65)';

interface AccountPoint {
  name: string;
  active: number;          // active deprecated functions in the account
  avgDays: number;         // average days to deprecation (urgency)
  critical: number;        // CRITICAL-priority functions (bubble size)
}

function buildPoints(functions: MigrationFunction[]): AccountPoint[] {
  const byAccount = new Map<string, { name: string; active: number; daysSum: number; daysCount: number; critical: number }>();
  for (const f of functions) {
    if (!isActiveBacklog(f)) continue;
    const id = f.account_id || 'this-account';
    const name = f.account_name || f.account_id || 'This account';
    const agg = byAccount.get(id) || { name, active: 0, daysSum: 0, daysCount: 0, critical: 0 };
    agg.active += 1;
    if (typeof f.days_to_deprecation === 'number') {
      agg.daysSum += f.days_to_deprecation;
      agg.daysCount += 1;
    }
    if ((f.priority_label || '').toUpperCase() === 'CRITICAL') agg.critical += 1;
    byAccount.set(id, agg);
  }
  return [...byAccount.values()]
    .map((a) => ({ name: a.name, active: a.active, avgDays: a.daysCount ? a.daysSum / a.daysCount : 0, critical: a.critical }))
    .sort((a, b) => a.active - b.active); // ascending x (Highcharts error #15 guard)
}

export default function AccountRiskBubbleChart({ functions }: { functions: MigrationFunction[] }) {
  const points = useMemo(() => buildPoints(functions), [functions]);

  const options = useMemo<Highcharts.Options>(() => ({
    chart: { type: 'bubble', height: 320 },
    title: { text: undefined },
    credits: { enabled: false },
    accessibility: {
      enabled: true,
      description: 'Bubble chart showing each account by number of active deprecated functions and urgency.',
    },
    legend: { enabled: false },
    xAxis: {
      title: { text: 'Active deprecated functions' },
      min: 0,
      allowDecimals: false,
    },
    yAxis: {
      title: { text: 'Avg days to deprecation (sooner = up)' },
      reversed: true, // smaller days (more urgent) reads higher
    },
    tooltip: {
      useHTML: true,
      pointFormat: '<b>{point.name}</b><br/>Active: {point.x}<br/>Avg days to EOL: {point.y:.0f}<br/>Critical: {point.z}',
    },
    series: [
      {
        type: 'bubble',
        name: 'Accounts',
        color: BUBBLE_COLOR,
        minSize: 12,
        maxSize: 56,
        data: points.map((p) => ({ x: p.active, y: Math.round(p.avgDays), z: Math.max(p.critical, 1), name: p.name })),
        dataLabels: {
          enabled: true,
          format: '{point.name}',
          style: { fontWeight: 'normal', textOutline: 'none' },
        },
      } as Highcharts.SeriesBubbleOptions,
    ],
  }), [points]);

  if (points.length === 0) {
    return <Box color="text-body-secondary" textAlign="center" padding="l">There is no data available.</Box>;
  }
  return <HighchartsReact options={options} ariaLabel="Account risk map bubble chart" />;
}
