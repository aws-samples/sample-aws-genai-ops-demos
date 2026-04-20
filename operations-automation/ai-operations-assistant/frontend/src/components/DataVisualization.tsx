import { useState } from 'react';
import Table from '@cloudscape-design/components/table';
import BarChart from '@cloudscape-design/components/bar-chart';
import LineChart from '@cloudscape-design/components/line-chart';
import Cards from '@cloudscape-design/components/cards';
import Box from '@cloudscape-design/components/box';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Badge from '@cloudscape-design/components/badge';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type DataType = 'tabular' | 'timeseries' | 'recommendations' | 'crossdomain' | 'narrative';

export interface TabularData {
  type: 'tabular';
  columns: string[];
  rows: Record<string, string | number>[];
}

export interface TimeSeriesData {
  type: 'timeseries';
  chartType?: 'bar' | 'line';
  title?: string;
  xLabel?: string;
  yLabel?: string;
  series: { label: string; data: { x: string; y: number }[] }[];
}

export interface Recommendation {
  title: string;
  description: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  source?: string;
}

export interface RecommendationsData {
  type: 'recommendations';
  items: Recommendation[];
}

export interface CrossDomainGroup {
  domain: string;
  content: string | TabularData | RecommendationsData;
}

export interface CrossDomainData {
  type: 'crossdomain';
  groups: CrossDomainGroup[];
}

export type StructuredData = TabularData | TimeSeriesData | RecommendationsData | CrossDomainData;

export interface DataVisualizationProps {
  data: string | StructuredData;
}

// ---------------------------------------------------------------------------
// Exported utility: classify data type
// ---------------------------------------------------------------------------

export function classifyData(data: string | StructuredData): DataType {
  if (typeof data !== 'string') {
    if ('type' in data) {
      const t = data.type;
      if (t === 'tabular' || t === 'timeseries' || t === 'recommendations' || t === 'crossdomain') {
        return t;
      }
    }
    return 'narrative';
  }
  return 'narrative';
}

// ---------------------------------------------------------------------------
// Exported utility: simple regex-based markdown to HTML
// ---------------------------------------------------------------------------

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function renderMarkdown(md: string): string {
  let html = md;

  // Code blocks
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code: string) =>
    '<pre><code>' + escapeHtml(code.trimEnd()) + '</code></pre>'
  );

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Headers
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

  // Bold + italic combined, then bold, then italic
  html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

  // Unordered list items
  html = html.replace(/^[-*] (.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li>[\s\S]*?<\/li>\n?)+/g, (match) => '<ul>' + match + '</ul>');

  // Ordered list items
  html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');

  // Double newlines to paragraph breaks
  html = html.replace(/\n\n/g, '</p><p>');
  html = html.replace(/\n/g, '<br/>');

  if (!html.startsWith('<')) {
    html = '<p>' + html + '</p>';
  }

  return html;
}

// ---------------------------------------------------------------------------
// Exported utility: should collapse (> 500 words)
// ---------------------------------------------------------------------------

export function shouldCollapse(text: string): boolean {
  const wordCount = text.trim().split(/\s+/).filter(Boolean).length;
  return wordCount > 500;
}

// ---------------------------------------------------------------------------
// Severity color mapping
// ---------------------------------------------------------------------------

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#d13212',
  high: '#ff9900',
  medium: '#f2c744',
  low: '#037f0c',
  info: '#0972d3',
};

// ---------------------------------------------------------------------------
// Sub-renderers
// ---------------------------------------------------------------------------

function TabularView({ data }: { data: TabularData }) {
  const [sortingColumn, setSortingColumn] = useState<string | undefined>();
  const [sortingDescending, setSortingDescending] = useState(false);

  const columnDefs = data.columns.map((col) => ({
    id: col,
    header: col,
    cell: (item: Record<string, string | number>) => String(item[col] ?? ''),
    sortingField: col,
  }));

  const sortedRows = [...data.rows];
  if (sortingColumn) {
    sortedRows.sort((a, b) => {
      const aVal = a[sortingColumn] ?? '';
      const bVal = b[sortingColumn] ?? '';
      const cmp = String(aVal).localeCompare(String(bVal), undefined, { numeric: true });
      return sortingDescending ? -cmp : cmp;
    });
  }

  return (
    <Table
      columnDefinitions={columnDefs}
      items={sortedRows}
      sortingColumn={sortingColumn ? { sortingField: sortingColumn } : undefined}
      sortingDescending={sortingDescending}
      onSortingChange={({ detail }) => {
        setSortingColumn(detail.sortingColumn?.sortingField);
        setSortingDescending(detail.isDescending ?? false);
      }}
      variant="embedded"
      empty={<Box textAlign="center">No data</Box>}
    />
  );
}

function TimeSeriesView({ data }: { data: TimeSeriesData }) {
  const chartSeries = data.series.map((s) => ({
    title: s.label,
    type: 'bar' as const,
    data: s.data.map((d) => ({ x: d.x, y: d.y })),
  }));

  const lineSeries = data.series.map((s) => ({
    title: s.label,
    type: 'line' as const,
    data: s.data.map((d) => ({ x: new Date(d.x), y: d.y })),
  }));

  if (data.chartType === 'line') {
    return (
      <LineChart
        series={lineSeries}
        xTitle={data.xLabel ?? 'Date'}
        yTitle={data.yLabel ?? 'Cost ($)'}
        height={300}
        empty={<Box textAlign="center">No data</Box>}
        noMatch={<Box textAlign="center">No matching data</Box>}
      />
    );
  }

  return (
    <BarChart
      series={chartSeries}
      xTitle={data.xLabel ?? 'Period'}
      yTitle={data.yLabel ?? 'Cost ($)'}
      height={300}
      empty={<Box textAlign="center">No data</Box>}
      noMatch={<Box textAlign="center">No matching data</Box>}
    />
  );
}

function RecommendationsView({ data }: { data: RecommendationsData }) {
  return (
    <Cards
      items={data.items}
      cardDefinition={{
        header: (item) => (
          <SpaceBetween direction="horizontal" size="xs">
            <span
              style={{
                display: 'inline-block',
                width: 10,
                height: 10,
                borderRadius: '50%',
                backgroundColor: SEVERITY_COLORS[item.severity] ?? SEVERITY_COLORS.info,
                marginRight: 6,
              }}
            />
            {item.title}
            <Badge color={item.severity === 'critical' || item.severity === 'high' ? 'red' : item.severity === 'medium' ? 'blue' : 'green'}>
              {item.severity}
            </Badge>
          </SpaceBetween>
        ),
        sections: [
          { id: 'description', content: (item: Recommendation) => item.description },
          ...(data.items.some((i) => i.source)
            ? [{ id: 'source', header: 'Source', content: (item: Recommendation) => item.source ?? '' }]
            : []),
        ],
      }}
      empty={<Box textAlign="center">No recommendations</Box>}
    />
  );
}

function CrossDomainView({ data }: { data: CrossDomainData }) {
  return (
    <SpaceBetween size="l">
      {data.groups.map((group, idx) => (
        <div key={idx}>
          <Header variant="h3">
            <Badge>{group.domain}</Badge>
          </Header>
          <Box padding={{ top: 'xs' }}>
            {typeof group.content === 'string' ? (
              <NarrativeView text={group.content} />
            ) : group.content.type === 'tabular' ? (
              <TabularView data={group.content} />
            ) : group.content.type === 'recommendations' ? (
              <RecommendationsView data={group.content} />
            ) : (
              <NarrativeView text={JSON.stringify(group.content)} />
            )}
          </Box>
        </div>
      ))}
    </SpaceBetween>
  );
}

function NarrativeView({ text }: { text: string }) {
  const html = renderMarkdown(text);
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function DataVisualization({ data }: DataVisualizationProps) {
  const dataType = classifyData(data);

  // For string data, check if it should be collapsed
  if (typeof data === 'string') {
    if (shouldCollapse(data)) {
      return (
        <ExpandableSection headerText="Response (click to expand)" defaultExpanded={false}>
          <NarrativeView text={data} />
        </ExpandableSection>
      );
    }
    return <NarrativeView text={data} />;
  }

  // Structured data rendering
  switch (dataType) {
    case 'tabular':
      return <TabularView data={data as TabularData} />;
    case 'timeseries':
      return <TimeSeriesView data={data as TimeSeriesData} />;
    case 'recommendations':
      return <RecommendationsView data={data as RecommendationsData} />;
    case 'crossdomain':
      return <CrossDomainView data={data as CrossDomainData} />;
    default:
      return <NarrativeView text={JSON.stringify(data)} />;
  }
}
