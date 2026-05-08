import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Container,
  ContentLayout,
  Header,
  Link,
  SpaceBetween,
  Table,
} from '@cloudscape-design/components';
import { CostEvent, CostSummary, getCostSummary, listCostEvents } from '../api';
import { COLOR } from '../theme';

const TYPE_LABEL: Record<string, string> = {
  bedrock_insights: 'Bedrock Insights',
  devops_agent_dispatch: 'DevOps Agent dispatch',
  scan: 'Prowler scan',
};
const TYPE_COLOR: Record<string, string> = {
  bedrock_insights: 'var(--soc-accent)',
  devops_agent_dispatch: 'var(--soc-high)',
  scan: 'var(--soc-ok)',
};

function fmtUsd(n: number | undefined, digits = 4): string {
  if (n === undefined || n === null || Number.isNaN(n)) return '—';
  if (n === 0) return '$0.0000';
  if (n < 0.0001) return `<$0.0001`;
  return `$${n.toFixed(digits)}`;
}

function fmtInt(n: number | undefined): string {
  if (n === undefined || n === null) return '—';
  return n.toLocaleString();
}

function relativeTime(iso?: string): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diff = Math.max(0, Date.now() - then);
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return new Date(iso).toLocaleString();
}

/** SVG line chart of cumulative cost vs time. */
function CumulativeChart({ events }: { events: CostEvent[] }) {
  const width = 760;
  const height = 200;
  const padding = { top: 14, right: 20, bottom: 26, left: 52 };

  const sorted = useMemo(() => {
    return [...events]
      .filter((e) => e.created_at)
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
  }, [events]);

  if (sorted.length === 0) {
    return (
      <Box color="text-status-inactive" textAlign="center" padding="l">
        No cost events yet. Cost data appears after scans, Bedrock Insights, or DevOps Agent dispatches.
      </Box>
    );
  }

  let running = 0;
  const points = sorted.map((e) => {
    running += e.cost_usd || 0;
    return { t: new Date(e.created_at).getTime(), v: running, type: e.event_type };
  });

  const minT = points[0].t;
  const maxT = points[points.length - 1].t || minT + 1;
  const maxV = Math.max(...points.map((p) => p.v), 0.0001);

  const xScale = (t: number) => {
    if (maxT === minT) return padding.left + (width - padding.left - padding.right) / 2;
    return padding.left + ((t - minT) / (maxT - minT)) * (width - padding.left - padding.right);
  };
  const yScale = (v: number) => height - padding.bottom - (v / maxV) * (height - padding.top - padding.bottom);

  const pathD = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${xScale(p.t).toFixed(1)} ${yScale(p.v).toFixed(1)}`)
    .join(' ');

  const areaD = `${pathD} L ${xScale(points[points.length - 1].t).toFixed(1)} ${height - padding.bottom} L ${xScale(points[0].t).toFixed(1)} ${height - padding.bottom} Z`;

  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({ v: maxV * f, y: yScale(maxV * f) }));

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 'auto', overflow: 'visible' }}>
      <defs>
        <linearGradient id="cost-area-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--soc-accent)" stopOpacity="0.4" />
          <stop offset="100%" stopColor="var(--soc-accent)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {/* Grid lines */}
      {yTicks.map((t, i) => (
        <g key={i}>
          <line x1={padding.left} y1={t.y} x2={width - padding.right} y2={t.y}
                stroke="var(--soc-border)" strokeWidth={1} strokeDasharray="3 3" opacity={0.5} />
          <text x={padding.left - 8} y={t.y + 3} textAnchor="end" fontSize={10} fill="var(--soc-fg-muted)" fontFamily="JetBrains Mono, monospace">
            {fmtUsd(t.v)}
          </text>
        </g>
      ))}
      {/* Area fill */}
      <path d={areaD} fill="url(#cost-area-grad)" />
      {/* Line */}
      <path d={pathD} fill="none" stroke="var(--soc-accent)" strokeWidth={2}
            style={{ filter: 'drop-shadow(0 0 6px var(--soc-accent-glow))' }} />
      {/* Dots */}
      {points.map((p, i) => (
        <circle key={i} cx={xScale(p.t)} cy={yScale(p.v)} r={3}
                fill={TYPE_COLOR[p.type] || 'var(--soc-accent)'} stroke="var(--soc-bg-elev)" strokeWidth={1.5}>
          <title>{TYPE_LABEL[p.type] || p.type} · {fmtUsd(p.v)} cumulative · {new Date(p.t).toLocaleString()}</title>
        </circle>
      ))}
      {/* X axis labels: first + last */}
      <text x={padding.left} y={height - 8} fontSize={10} fill="var(--soc-fg-muted)" fontFamily="JetBrains Mono, monospace">
        {new Date(minT).toLocaleTimeString()}
      </text>
      <text x={width - padding.right} y={height - 8} fontSize={10} fill="var(--soc-fg-muted)" textAnchor="end" fontFamily="JetBrains Mono, monospace">
        {new Date(maxT).toLocaleTimeString()}
      </text>
    </svg>
  );
}

/** Pure-SVG donut (duplicated intentionally to keep page self-contained). */
function Donut({ data, size = 180 }: { data: Array<{ label: string; value: number; color: string }>; size?: number }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  if (total === 0) return <Box color="text-status-inactive" textAlign="center" padding="l">No events</Box>;
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 8;
  const innerR = r * 0.62;
  let angle = -Math.PI / 2;
  const arcs = data.filter((d) => d.value > 0).map((d) => {
    const frac = d.value / total;
    const a2 = angle + frac * Math.PI * 2;
    const x1 = cx + Math.cos(angle) * r;
    const y1 = cy + Math.sin(angle) * r;
    const x2 = cx + Math.cos(a2) * r;
    const y2 = cy + Math.sin(a2) * r;
    const xi2 = cx + Math.cos(a2) * innerR;
    const yi2 = cy + Math.sin(a2) * innerR;
    const xi1 = cx + Math.cos(angle) * innerR;
    const yi1 = cy + Math.sin(angle) * innerR;
    const large = frac > 0.5 ? 1 : 0;
    const path = `M ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} L ${xi2} ${yi2} A ${innerR} ${innerR} 0 ${large} 0 ${xi1} ${yi1} Z`;
    angle = a2;
    return { path, color: d.color, label: d.label, value: d.value, frac };
  });
  return (
    <div style={{ display: 'flex', gap: 18, alignItems: 'center', justifyContent: 'center', flexWrap: 'wrap' }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {arcs.map((a, i) => <path key={i} d={a.path} fill={a.color} stroke="var(--soc-bg-elev)" strokeWidth={2} />)}
        <text x="50%" y="48%" textAnchor="middle" fontSize={size * 0.14} fontWeight={700} fill="currentColor">
          {fmtUsd(total, 4)}
        </text>
        <text x="50%" y="62%" textAnchor="middle" fontSize={size * 0.07} fill="var(--soc-fg-muted)" letterSpacing="0.08em">
          TOTAL
        </text>
      </svg>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 180 }}>
        {arcs.map((a) => (
          <div key={a.label} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <span style={{ display: 'inline-block', width: 10, height: 10, background: a.color, borderRadius: 2 }} />
            <span style={{ color: COLOR.fg, fontWeight: 600, flex: 1 }}>{a.label}</span>
            <span style={{ color: COLOR.fgMuted, fontVariantNumeric: 'tabular-nums' }}>{fmtUsd(a.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Cost() {
  const navigate = useNavigate();
  const [events, setEvents] = useState<CostEvent[]>([]);
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const [ev, sm] = await Promise.all([listCostEvents(200), getCostSummary()]);
      setEvents(ev.events || []);
      setSummary(sm);
      if (sm.error) setError(sm.error);
    } catch (e: any) {
      setError(e?.message || 'Failed to load cost data');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, []);

  const total = summary?.total_usd ?? 0;
  const bedrock = summary?.by_type?.bedrock_insights?.cost_usd ?? 0;
  const bedrockCount = summary?.by_type?.bedrock_insights?.count ?? 0;
  const devops = summary?.by_type?.devops_agent_dispatch?.cost_usd ?? 0;
  const devopsCount = summary?.by_type?.devops_agent_dispatch?.count ?? 0;
  const scanCost = summary?.by_type?.scan?.cost_usd ?? 0;
  const scanCount = summary?.by_type?.scan?.count ?? 0;

  const donutData = useMemo(() => {
    const by = summary?.by_type || {};
    return Object.entries(by).map(([k, v]) => ({
      label: TYPE_LABEL[k] || k,
      value: v.cost_usd || 0,
      color: TYPE_COLOR[k] || 'var(--soc-medium)',
    }));
  }, [summary]);

  return (
    <ContentLayout
      header={
        <div className="soc-hero">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 24, flexWrap: 'wrap' }}>
            <div>
              <h1>Cost & GenAI telemetry</h1>
              <div className="soc-hero-sub">
                Every Bedrock invocation, DevOps Agent dispatch, and Prowler scan — live, per-event, with token-level pricing.
              </div>
              <div style={{ marginTop: 10, fontSize: 12, color: COLOR.fgDim, maxWidth: 720 }}>
                Prices are hardcoded for the demo (Nova Pro: $0.0008 in / $0.0032 out per 1K tokens · DevOps Agent: flat $0.50 per incident · Prowler scan: flat $0.02 per run). Real billing lives in AWS Cost Explorer.
              </div>
            </div>
            <Button iconName="refresh" onClick={load} loading={loading}>Refresh</Button>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginTop: 24 }}>
            <div className="soc-kpi soc-kpi--accent">
              <div className="soc-kpi-label">Total (demo)</div>
              <div className="soc-kpi-value">{loading && !summary ? '…' : fmtUsd(total, 4)}</div>
              <div className="soc-kpi-hint">{summary?.total_events ?? 0} events</div>
            </div>
            <div className="soc-kpi">
              <div className="soc-kpi-label">Bedrock Insights</div>
              <div className="soc-kpi-value">{loading && !summary ? '…' : fmtUsd(bedrock, 4)}</div>
              <div className="soc-kpi-hint">
                {bedrockCount} invocation(s) · {fmtInt(summary?.total_input_tokens)} in / {fmtInt(summary?.total_output_tokens)} out
              </div>
            </div>
            <div className="soc-kpi soc-kpi--high">
              <div className="soc-kpi-label">DevOps Agent</div>
              <div className="soc-kpi-value">{loading && !summary ? '…' : fmtUsd(devops, 2)}</div>
              <div className="soc-kpi-hint">{devopsCount} dispatch(es)</div>
            </div>
            <div className="soc-kpi soc-kpi--ok">
              <div className="soc-kpi-label">Prowler scans</div>
              <div className="soc-kpi-value">{loading && !summary ? '…' : fmtUsd(scanCost, 2)}</div>
              <div className="soc-kpi-hint">{scanCount} scan(s)</div>
            </div>
          </div>
        </div>
      }
    >
      <SpaceBetween size="l">
        {error && (
          <Alert type="warning" dismissible onDismiss={() => setError(null)}>
            {error}
          </Alert>
        )}

        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16 }}>
          <Container header={<Header variant="h2" description="Running USD total since the demo started">Cumulative cost</Header>}>
            <CumulativeChart events={events} />
          </Container>
          <Container header={<Header variant="h2" description="Where the demo's compute budget is spent">Breakdown by capability</Header>}>
            <Donut data={donutData} size={200} />
          </Container>
        </div>

        <Container
          header={
            <Header
              variant="h2"
              description="Newest first · click a finding-linked event to jump to its detail"
              counter={`(${events.length})`}
            >
              Cost events
            </Header>
          }
        >
          <Table
            loading={loading && events.length === 0}
            loadingText="Querying cost events…"
            items={events}
            trackBy="event_id"
            variant="embedded"
            empty={
              <Box textAlign="center" padding={{ vertical: 'l' }}>
                <Box color="text-status-inactive">No cost events yet.</Box>
                <Box variant="small" color="text-status-inactive">
                  Trigger a scan, generate Bedrock Insights on a finding, or dispatch a DevOps Agent investigation.
                </Box>
              </Box>
            }
            columnDefinitions={[
              {
                id: 'when',
                header: 'When',
                cell: (e) => <span style={{ color: COLOR.fgMuted, fontFamily: 'JetBrains Mono, monospace', fontSize: 12 }}>{relativeTime(e.created_at)}</span>,
                minWidth: 110,
              },
              {
                id: 'type',
                header: 'Type',
                cell: (e) => (
                  <span style={{
                    display: 'inline-flex', alignItems: 'center', gap: 8,
                    padding: '3px 10px', borderRadius: 999,
                    border: `1px solid ${TYPE_COLOR[e.event_type] || 'var(--soc-border)'}`,
                    color: TYPE_COLOR[e.event_type] || COLOR.fg,
                    fontSize: 11, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase',
                  }}>
                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: TYPE_COLOR[e.event_type] || 'var(--soc-border)' }} />
                    {TYPE_LABEL[e.event_type] || e.event_type}
                  </span>
                ),
                minWidth: 180,
              },
              {
                id: 'cost',
                header: 'Cost',
                cell: (e) => <span style={{ color: COLOR.fg, fontWeight: 700, fontVariantNumeric: 'tabular-nums', fontFamily: 'JetBrains Mono, monospace' }}>{fmtUsd(e.cost_usd, 4)}</span>,
                minWidth: 90,
              },
              {
                id: 'tokens',
                header: 'Tokens (in / out)',
                cell: (e) => {
                  if (!e.input_tokens && !e.output_tokens) return <span style={{ color: COLOR.fgDim }}>—</span>;
                  return (
                    <span style={{ color: COLOR.fgMuted, fontFamily: 'JetBrains Mono, monospace', fontSize: 12 }}>
                      {fmtInt(e.input_tokens)} / {fmtInt(e.output_tokens)}
                    </span>
                  );
                },
                minWidth: 150,
              },
              {
                id: 'model',
                header: 'Model',
                cell: (e) => e.model_id ? <code style={{ color: COLOR.fgMuted, fontSize: 11 }}>{e.model_id}</code> : <span style={{ color: COLOR.fgDim }}>—</span>,
                minWidth: 200,
              },
              {
                id: 'finding',
                header: 'Finding',
                cell: (e) => e.finding_uid ? (
                  <Link onFollow={(ev) => { ev.preventDefault(); navigate(`/findings/${encodeURIComponent(e.finding_uid!)}`); }} href={`/findings/${encodeURIComponent(e.finding_uid)}`}>
                    <code style={{ fontSize: 11 }}>{e.finding_uid.length > 48 ? `${e.finding_uid.slice(0, 45)}…` : e.finding_uid}</code>
                  </Link>
                ) : <span style={{ color: COLOR.fgDim }}>—</span>,
                minWidth: 280,
              },
            ]}
          />
        </Container>
      </SpaceBetween>
    </ContentLayout>
  );
}
