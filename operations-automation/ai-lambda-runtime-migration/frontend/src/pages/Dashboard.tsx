import { useState, useEffect } from 'react';
import {
  Box,
  Button,
  Header,
  SpaceBetween,
  Spinner,
  Alert,
  StatusIndicator,
} from '@cloudscape-design/components';
import PieChart from '@cloudscape-design/chart-components/pie-chart';
import CartesianChart from '@cloudscape-design/chart-components/cartesian-chart';
import Highcharts from 'highcharts';
import Board, { BoardProps } from '@cloudscape-design/board-components/board';
import BoardItem from '@cloudscape-design/board-components/board-item';
import { getStatusConfig } from '../constants';
import { invokeDiscover, readInventory } from '../agentcore';

interface FunctionRecord {
  function_arn: string;
  runtime?: string;
  migration_complexity?: string;
  migration_status?: string;
  alert_status?: string;
  priority_score?: number;
  [key: string]: unknown;
}

interface DiscoverResponse {
  functions: FunctionRecord[];
  total: number;
  message?: string;
}

const RUNTIME_COLORS = [
  '#688ae8', '#c33d69', '#2ea597', '#8b5cf6',
  '#e07941', '#3b82f6', '#ec4899', '#059669',
];

function computeSummary(functions: FunctionRecord[]) {
  const byRuntime: Record<string, number> = {};
  const byComplexity: Record<string, number> = {};
  const byStatus: Record<string, number> = {};
  const byAlert: Record<string, number> = {};

  for (const fn of functions) {
    const rt = fn.runtime || 'unknown';
    byRuntime[rt] = (byRuntime[rt] || 0) + 1;

    const cx = fn.migration_complexity || 'UNKNOWN';
    byComplexity[cx] = (byComplexity[cx] || 0) + 1;

    const st = fn.migration_status || 'DISCOVERED';
    byStatus[st] = (byStatus[st] || 0) + 1;

    const al = (fn.alert_status || 'unknown').toLowerCase();
    byAlert[al] = (byAlert[al] || 0) + 1;
  }

  return { byRuntime, byComplexity, byStatus, byAlert };
}

const BOARD_I18N = {
  liveAnnouncementDndStarted: () => '',
  liveAnnouncementDndItemReordered: () => '',
  liveAnnouncementDndItemResized: () => '',
  liveAnnouncementDndItemInserted: () => '',
  liveAnnouncementDndCommitted: () => '',
  liveAnnouncementDndDiscarded: () => '',
  liveAnnouncementItemRemoved: () => '',
  navigationAriaLabel: 'Dashboard widgets',
  navigationItemAriaLabel: () => '',
};

const BOARD_ITEM_I18N = {
  dragHandleAriaLabel: 'Drag handle',
  resizeHandleAriaLabel: 'Resize handle',
};

interface WidgetData {
  title: string;
  description?: string;
  content: React.ReactNode;
}

export default function Dashboard() {
  const [functions, setFunctions] = useState<FunctionRecord[]>([]);
  const [scanning, setScanning] = useState(false);
  const [scanMessage, setScanMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hasScanned, setHasScanned] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);

  useEffect(() => {
    loadInventoryData();
  }, []);

  async function loadInventoryData() {
    try {
      let result = await readInventory();
      if (typeof result === 'string') {
        try { result = JSON.parse(result); } catch { /* keep */ }
      }
      const typed = result as DiscoverResponse | null;
      if (typed?.functions?.length) {
        setFunctions(typed.functions);
        setScanMessage(typed.message || `Loaded ${typed.total || 0} functions from inventory.`);
        setHasScanned(true);
      }
    } catch {
      // Silent fail — user can trigger scan manually
    } finally {
      setInitialLoading(false);
    }
  }

  async function handleTriggerScan() {
    setScanning(true);
    setScanMessage(null);
    setError(null);
    try {
      const result = await invokeDiscover();
      let parsed: DiscoverResponse | null = null;
      if (typeof result === 'string') {
        try { parsed = JSON.parse(result); } catch { parsed = null; }
      } else if (result && typeof result === 'object') {
        parsed = result as DiscoverResponse;
      }
      if (!parsed || !Array.isArray(parsed.functions)) {
        setScanMessage('Scan completed but no data was returned. Try again in a moment.');
        setHasScanned(true);
        return;
      }
      setFunctions(parsed.functions);
      setScanMessage(parsed.message || `Discovered ${parsed.total || 0} functions.`);
      setHasScanned(true);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to trigger scan';
      setError(`Scan failed: ${msg}`);
    } finally {
      setScanning(false);
    }
  }

  const summary = computeSummary(functions);
  const total = functions.length;

  const runtimeData = Object.entries(summary.byRuntime).map(([name, y], i) => ({
    name, y, color: RUNTIME_COLORS[i % RUNTIME_COLORS.length],
  }));

  const complexityData = [
    { name: 'Low', y: summary.byComplexity.LOW || 0 },
    { name: 'Medium', y: summary.byComplexity.MEDIUM || 0 },
    { name: 'High', y: summary.byComplexity.HIGH || 0 },
  ];

  function buildBoardItems(): BoardProps.Item<WidgetData>[] {
    return [
      {
        id: 'total',
        definition: { defaultRowSpan: 2, defaultColumnSpan: 1, minRowSpan: 2 },
        data: {
          title: 'Total Functions',
          content: (
            <Box textAlign="center">
              <Box variant="h1" fontSize="display-l">{total}</Box>
              <Box variant="small" color="text-body-secondary">deprecated-runtime functions</Box>
            </Box>
          ),
        },
      },
      {
        id: 'ta-status',
        definition: { defaultRowSpan: 3, defaultColumnSpan: 2, minRowSpan: 2 },
        data: {
          title: 'Deprecation Timeline',
          description: 'AWS Trusted Advisor — "AWS Lambda Functions Using Deprecated Runtimes" (L4dfs2Q4C5)',
          content: (() => {
            // Build timeline data from functions
            const redPoints: { x: number; y: number; name: string }[] = [];
            const yellowPoints: { x: number; y: number; name: string }[] = [];
            for (const fn of functions) {
              const dateStr = fn.deprecation_date as string;
              if (!dateStr) continue;
              const ts = new Date(dateStr).getTime();
              if (isNaN(ts)) continue;
              const name = (fn.function_arn.match(/function:(.+)/)?.[1] || fn.function_arn).split(':')[0];
              const alert = ((fn.alert_status || '') as string).toLowerCase();
              const point = { x: ts, y: 1, name };
              if (alert === 'red') redPoints.push(point);
              else yellowPoints.push(point);
            }
            const series: any[] = [];
            if (redPoints.length) series.push({ type: 'scatter', name: 'Deprecated (Red)', data: redPoints, color: '#d91515' });
            if (yellowPoints.length) series.push({ type: 'scatter', name: 'Upcoming (Yellow)', data: yellowPoints, color: '#f2a900' });
            if (!series.length) return <Box textAlign="center" color="text-status-inactive">No deprecation data</Box>;

            return (
              <CartesianChart
                highcharts={Highcharts}
                series={series}
                xAxis={{
                  type: 'datetime',
                  title: 'Deprecation Date',
                }}
                yAxis={{}}
                tooltip={{ enabled: true }}
              />
            );
          })(),
        },
      },
      {
        id: 'migration-status',
        definition: { defaultRowSpan: 2, defaultColumnSpan: 1, minRowSpan: 2 },
        data: {
          title: 'Migration Status',
          content: (
            <SpaceBetween size="xs">
              {Object.entries(summary.byStatus).map(([status, count]) => {
                const cfg = getStatusConfig(status);
                return (
                  <StatusIndicator key={status} type={cfg.type}>
                    {cfg.label}: {count}
                  </StatusIndicator>
                );
              })}
            </SpaceBetween>
          ),
        },
      },
      {
        id: 'complexity',
        definition: { defaultRowSpan: 2, defaultColumnSpan: 1, minRowSpan: 2 },
        data: {
          title: 'Complexity Breakdown',
          content: (
            <SpaceBetween size="xs">
              <Box>Low: {summary.byComplexity.LOW || 0}</Box>
              <Box>Medium: {summary.byComplexity.MEDIUM || 0}</Box>
              <Box>High: {summary.byComplexity.HIGH || 0}</Box>
            </SpaceBetween>
          ),
        },
      },
      {
        id: 'runtime-chart',
        definition: { defaultRowSpan: 4, defaultColumnSpan: 2, minRowSpan: 3 },
        data: {
          title: 'Functions by Runtime',
          content: runtimeData.length > 0 ? (
            <PieChart
              highcharts={Highcharts}
              series={{ type: 'donut', name: 'Runtimes', data: runtimeData }}
              innerAreaTitle={String(total)}
              innerAreaDescription="functions"
            />
          ) : (
            <Box textAlign="center" color="text-status-inactive">No runtime data</Box>
          ),
        },
      },
      {
        id: 'complexity-chart',
        definition: { defaultRowSpan: 4, defaultColumnSpan: 2, minRowSpan: 3 },
        data: {
          title: 'Functions by Complexity',
          content: complexityData.some((d) => d.y > 0) ? (
            <CartesianChart
              highcharts={Highcharts}
              series={[{ type: 'column', name: 'Functions', data: complexityData, color: '#688ae8' }]}
              xAxis={{ type: 'category', title: 'Complexity' }}
              yAxis={{ title: 'Count' }}
            />
          ) : (
            <Box textAlign="center" color="text-status-inactive">No complexity data yet — run analysis first</Box>
          ),
        },
      },
    ];
  }

  const [boardItems, setBoardItems] = useState<BoardProps.Item<WidgetData>[]>([]);

  useEffect(() => {
    if (hasScanned) {
      setBoardItems(buildBoardItems());
    }
  }, [functions, hasScanned]);

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Discover, assess, and transform AWS Lambda functions running deprecated runtimes using Amazon Bedrock and AgentCore"
        actions={
          <SpaceBetween size="xs" direction="horizontal">
            <Button onClick={handleTriggerScan} loading={scanning} iconName="search">
              Scan
            </Button>
            <Button onClick={() => { loadInventoryData(); }} iconName="refresh">
              Refresh
            </Button>
          </SpaceBetween>
        }
      >
        Dashboard
      </Header>

      {error && (
        <Alert type="error" dismissible onDismiss={() => setError(null)}>{error}</Alert>
      )}
      {scanMessage && (
        <Alert type="info" dismissible onDismiss={() => setScanMessage(null)}>{scanMessage}</Alert>
      )}

      {(scanning || initialLoading) && (
        <Box textAlign="center" padding={{ top: 'xxxl' }}>
          <Spinner size="large" />
          <Box variant="p" padding={{ top: 's' }}>
            {scanning
              ? 'Discovering deprecated-runtime Lambda functions via Trusted Advisor... This may take a minute.'
              : 'Loading inventory...'}
          </Box>
        </Box>
      )}

      {!scanning && !initialLoading && !hasScanned && (
        <Box textAlign="center" padding={{ top: 'xxxl' }}>
          <Box variant="h2" padding={{ bottom: 's' }}>No functions discovered yet</Box>
          <Box variant="p" color="text-body-secondary">
            Click "Trigger Scan" to discover Lambda functions running deprecated runtimes
            via Trusted Advisor check L4dfs2Q4C5.
          </Box>
        </Box>
      )}

      {!scanning && !initialLoading && hasScanned && (
        <Board
          items={boardItems}
          onItemsChange={({ detail: { items } }) => setBoardItems(items as BoardProps.Item<WidgetData>[])}
          renderItem={(item) => (
            <BoardItem
              header={<Header description={item.data.description}>{item.data.title}</Header>}
              i18nStrings={BOARD_ITEM_I18N}
            >
              {item.data.content}
            </BoardItem>
          )}
          i18nStrings={BOARD_I18N}
          empty={<Box textAlign="center">No widgets</Box>}
        />
      )}
    </SpaceBetween>
  );
}
