// Chart 5 — "Risk vs usage" (scatter).
//
// Cloudscape v3 has no scatter chart, so this uses Highcharts. One point per
// function: x = days to deprecation, y = average daily invokes, colored by its
// Deprecation state (Deprecated = red, Approaching EOL = yellow, Migrated =
// green). Bottom-left = soonest EOL and busiest — the functions to fix first.
import { useMemo } from 'react';
import Highcharts from './highcharts-setup';
import Box from '@cloudscape-design/components/box';
import HighchartsReact from './HighchartsReact';
import { MigrationFunction } from '../../api';
import { funcName } from '../../console-links';
import { deprecationState, DeprecationState } from '../../migration-tallies';

// red = error, yellow = warning, green = success (status data-viz convention).
const STATE_COLOR: Record<string, string> = {
  Deprecated: '#d91515',
  'Approaching EOL': '#e07941',
  Migrated: '#037f0c',
};

type DeprecationLabel = DeprecationState['label'];

interface ScatterSeries {
  name: DeprecationLabel;
  data: Array<{ x: number; y: number; name: string }>;
}

function buildSeries(functions: MigrationFunction[]): ScatterSeries[] {
  const buckets: Record<DeprecationLabel, ScatterSeries> = {
    Deprecated: { name: 'Deprecated', data: [] },
    'Approaching EOL': { name: 'Approaching EOL', data: [] },
    Migrated: { name: 'Migrated', data: [] },
  };
  for (const f of functions) {
    const label = deprecationState(f)?.label ?? null;
    if (!label) continue;
    if (typeof f.days_to_deprecation !== 'number') continue;
    buckets[label].data.push({
      x: f.days_to_deprecation,
      y: f.avg_daily_invokes ?? 0,
      name: funcName(f.function_arn),
    });
  }
  // Sort each series ascending by x (Highcharts error #15 guard).
  return (Object.values(buckets) as ScatterSeries[])
    .map((s) => ({ ...s, data: [...s.data].sort((a, b) => a.x - b.x) }))
    .filter((s) => s.data.length > 0);
}

export default function RiskVsUsageScatterChart({ functions }: { functions: MigrationFunction[] }) {
  const series = useMemo(() => buildSeries(functions), [functions]);

  const options = useMemo<Highcharts.Options>(() => ({
    chart: { type: 'scatter', height: 320, zooming: { type: 'xy' } },
    title: { text: undefined },
    credits: { enabled: false },
    accessibility: {
      enabled: true,
      description: 'Scatter chart showing each function by days to deprecation and average daily invokes.',
    },
    legend: { enabled: true },
    xAxis: { title: { text: 'Days to deprecation' } },
    yAxis: { title: { text: 'Avg daily invokes' }, min: 0 },
    tooltip: {
      useHTML: true,
      headerFormat: '',
      pointFormat: '<b>{point.name}</b><br/>Days to EOL: {point.x}<br/>Avg daily invokes: {point.y}',
    },
    plotOptions: {
      scatter: { marker: { radius: 5, symbol: 'circle' } },
    },
    series: series.map((s) => ({
      type: 'scatter',
      name: s.name,
      color: STATE_COLOR[s.name],
      data: s.data,
    })) as Highcharts.SeriesScatterOptions[],
  }), [series]);

  if (series.length === 0) {
    return <Box color="text-body-secondary" textAlign="center" padding="l">There is no data available.</Box>;
  }
  return <HighchartsReact options={options} ariaLabel="Risk versus usage scatter chart" />;
}
