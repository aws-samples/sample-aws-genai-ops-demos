import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Container,
  ContentLayout,
  Header,
  LiveRegion,
  ProgressBar,
  SpaceBetween,
  StatusIndicator,
} from '@cloudscape-design/components';
import {
  getRunningScanLogs,
  listFindings,
  listRunningScans,
  listScans,
  runScan,
  Finding,
  RunningTask,
  ScanProgress,
} from '../api';
import { COLOR, severityRank } from '../theme';

function shortTaskId(arn: string): string {
  return arn.split('/').pop()!.slice(0, 10);
}

function countBy<T extends Record<string, any>>(arr: T[], key: keyof T) {
  const out: Record<string, number> = {};
  for (const v of arr) {
    const k = (v[key] as string) || 'UNKNOWN';
    out[k] = (out[k] || 0) + 1;
  }
  return out;
}

/** Pure-SVG radial gauge. */
function Gauge({ pct, label, size = 220 }: { pct: number; label: string; size?: number }) {
  const color = pct >= 80 ? 'var(--soc-ok)' : pct >= 50 ? 'var(--soc-medium)' : 'var(--soc-critical)';
  const stroke = 16;
  const r = (size - stroke) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = Math.PI * r;
  const dashOffset = circumference * (1 - pct / 100);
  return (
    <div style={{ textAlign: 'center' }}>
      <svg
        width={size}
        height={size * 0.7}
        viewBox={`0 0 ${size} ${size * 0.7}`}
        role="img"
        aria-label={`${label}: ${pct} percent`}
      >
        <title>{`${label}: ${pct}%`}</title>
        <path d={`M ${stroke / 2} ${cy} A ${r} ${r} 0 0 1 ${size - stroke / 2} ${cy}`}
              fill="none" stroke="var(--soc-border)" strokeWidth={stroke} strokeLinecap="round" />
        <path d={`M ${stroke / 2} ${cy} A ${r} ${r} 0 0 1 ${size - stroke / 2} ${cy}`}
              fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
              strokeDasharray={circumference} strokeDashoffset={dashOffset}
              className="soc-chart-anim"
              style={{ transition: 'stroke-dashoffset 0.6s ease' }} />
        <text x="50%" y={cy - 4} textAnchor="middle" fontFamily="Inter" fontSize={size * 0.22} fontWeight={700} fill="currentColor">
          {pct}%
        </text>
      </svg>
      <div style={{ fontSize: 11, color: COLOR.fgMuted, textTransform: 'uppercase', letterSpacing: '0.1em', marginTop: 2 }}>{label}</div>
    </div>
  );
}

/** Pure-SVG donut. */
function Donut({ data, size = 220 }: { data: Array<{ label: string; value: number; color: string }>; size?: number }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  if (total === 0) return <Box color="text-status-inactive" textAlign="center" padding="l">No data</Box>;
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 8;
  const innerR = r * 0.62;
  let angle = -Math.PI / 2;
  const arcs = data.map((d) => {
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
    return { path, color: d.color, label: d.label, value: d.value };
  });
  const summary = data.filter((d) => d.value > 0).map((d) => `${d.label}: ${d.value}`).join(', ');
  return (
    <div style={{ display: 'flex', gap: 24, alignItems: 'center', justifyContent: 'center', flexWrap: 'wrap' }}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={`Severity distribution. ${summary || 'No data'}`}
      >
        <title>Severity distribution</title>
        {arcs.map((a, i) => <path key={i} d={a.path} fill={a.color} stroke="var(--soc-bg-elev)" strokeWidth={2} />)}
      </svg>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {data.filter((d) => d.value > 0).map((d) => (
          <div key={d.label} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <span style={{ display: 'inline-block', width: 10, height: 10, background: d.color, borderRadius: 2 }} />
            <span style={{ color: COLOR.fg, fontWeight: 600 }}>{d.label}</span>
            <span style={{ color: COLOR.fgMuted }}>{d.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** HTML/CSS horizontal bar chart. */
function BarChart({ entries }: { entries: Array<[string, number]> }) {
  const max = Math.max(...entries.map((e) => e[1]), 1);
  return (
    <ul
      style={{ display: 'flex', flexDirection: 'column', gap: 6, margin: 0, padding: 0, listStyle: 'none' }}
      aria-label="Failing findings by service"
    >
      {entries.map(([name, count]) => {
        const pct = (count / max) * 100;
        const color = count >= 5 ? 'var(--soc-critical)' : count >= 3 ? 'var(--soc-high)' : 'var(--soc-accent)';
        return (
          <li
            key={name}
            style={{
              display: 'grid',
              // Service name column shrinks on mobile so the bar stays visible
              gridTemplateColumns: 'minmax(0, clamp(80px, 28%, 160px)) 1fr 44px',
              gap: 10,
              alignItems: 'center',
              minWidth: 0,
            }}
          >
            <div
              style={{ color: COLOR.fg, fontSize: 12, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}
              title={name}
            >
              {name}
            </div>
            <div
              style={{ background: 'var(--soc-border)', borderRadius: 4, height: 20, overflow: 'hidden' }}
              role="progressbar"
              aria-valuenow={count}
              aria-valuemin={0}
              aria-valuemax={max}
              aria-label={`${name}: ${count} failing`}
            >
              {/* Scale via transform (cheap, honours reduced-motion) instead of width */}
              <div
                className="soc-chart-anim"
                style={{
                  width: '100%',
                  height: '100%',
                  background: color,
                  transformOrigin: 'left center',
                  transform: `scaleX(${pct / 100})`,
                  transition: 'transform 0.5s ease',
                  boxShadow: `0 0 12px ${color}40`,
                }}
              />
            </div>
            <div style={{ color: COLOR.fg, fontWeight: 700, fontSize: 13, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{count}</div>
          </li>
        );
      })}
    </ul>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [findings, setFindings] = useState<Finding[]>([]);
  const [scans, setScans] = useState<Array<{ scan_id: string; last_seen_at: string }>>([]);
  const [running, setRunning] = useState<RunningTask[]>([]);
  const [progressByTask, setProgressByTask] = useState<Record<string, ScanProgress | null>>({});
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [res, sc, rn] = await Promise.all([
        listFindings({ limit: 500 }),
        listScans(),
        listRunningScans(),
      ]);
      setFindings(res.items || []);
      setScans(sc.scans || []);
      setRunning(rn.tasks || []);
    } catch (e: any) {
      setError(e?.message || 'Failed to load findings');
    } finally {
      setLoading(false);
    }
  }

  async function refreshRunning() {
    try {
      const rn = await listRunningScans();
      setRunning(rn.tasks || []);
    } catch { /* silent */ }
  }

  useEffect(() => { load(); }, []);

  const hasActiveScan = running.some((t) => t.lastStatus !== 'STOPPED');

  // Only re-install the interval when the boolean flips. Depending on the full
  // `running` array would tear down the timer on every 10s tick (because
  // refreshRunning sets a new array reference), effectively hammering the API.
  useEffect(() => {
    if (!hasActiveScan) return;
    const id = setInterval(() => {
      refreshRunning();
      listFindings({ limit: 500 }).then((r) => setFindings(r.items || [])).catch(() => {});
    }, 10000);
    return () => clearInterval(id);
  }, [hasActiveScan]);

  // Progress polling: while any task is live, fetch CloudWatch-parsed
  // progress every 5s for each RUNNING task. Kept separate from the main
  // refresh loop so the progress bar updates faster than the task-status one.
  useEffect(() => {
    const live = running.filter((t) => t.lastStatus === 'RUNNING' || t.lastStatus === 'PENDING');
    if (live.length === 0) return;
    let cancelled = false;
    const pull = async () => {
      const results = await Promise.allSettled(live.map((t) => getRunningScanLogs(t.taskArn)));
      if (cancelled) return;
      setProgressByTask((prev) => {
        const next = { ...prev };
        results.forEach((r, i) => {
          if (r.status === 'fulfilled') next[live[i].taskArn] = r.value.progress;
        });
        return next;
      });
    };
    pull();
    const id = setInterval(pull, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [running]);

  async function startScan() {
    setStarting(true);
    setMessage(null);
    setError(null);
    try {
      const res = await runScan();
      setMessage(`Scan dispatched — ${res.task_arns.length} Fargate task(s) launched.`);
      refreshRunning();
    } catch (e: any) {
      setError(e?.message || 'Failed to start scan');
    } finally {
      setStarting(false);
    }
  }

  // Compliance pass rate ignores suppressed findings (accepted risk etc.).
  const unsuppressed = useMemo(() => findings.filter((f) => !f.suppressed_at), [findings]);
  const total = findings.length;
  const bySeverity = useMemo(() => countBy(findings, 'severity'), [findings]);
  const byService = useMemo(
    () => countBy(unsuppressed.filter((f) => f.status === 'FAIL'), 'service_name'),
    [unsuppressed],
  );
  const byStatus = useMemo(() => countBy(unsuppressed, 'status'), [unsuppressed]);

  const failing = byStatus.FAIL || 0;
  const passing = byStatus.PASS || 0;
  const denominator = unsuppressed.length;
  const compliancePct = denominator === 0 ? 0 : Math.round((passing / denominator) * 100);

  const criticalFail = useMemo(() => findings.filter((f) => f.severity === 'CRITICAL' && f.status === 'FAIL').length, [findings]);
  const criticalTotal = useMemo(() => findings.filter((f) => f.severity === 'CRITICAL').length, [findings]);
  const highFail = useMemo(() => findings.filter((f) => f.severity === 'HIGH' && f.status === 'FAIL').length, [findings]);
  const highTotal = useMemo(() => findings.filter((f) => f.severity === 'HIGH').length, [findings]);
  const withInsights = useMemo(() => findings.filter((f) => Boolean(f.remediation_s3_key)).length, [findings]);
  const accountId = findings[0]?.account_id || '—';
  const region = findings[0]?.region || '—';

  const topCritical = useMemo(() => {
    return findings
      .filter((f) => f.status === 'FAIL')
      .sort((a, b) => severityRank(a.severity) - severityRank(b.severity))
      .slice(0, 6);
  }, [findings]);

  const donutData = [
    { label: 'CRITICAL', value: bySeverity.CRITICAL || 0, color: 'var(--soc-critical)' },
    { label: 'HIGH',     value: bySeverity.HIGH || 0,     color: 'var(--soc-high)' },
    { label: 'MEDIUM',   value: bySeverity.MEDIUM || 0,   color: 'var(--soc-medium)' },
    { label: 'LOW',      value: bySeverity.LOW || 0,      color: 'var(--soc-low)' },
    { label: 'INFO',     value: bySeverity.INFO || 0,     color: 'var(--soc-info)' },
  ];
  const barEntries = Object.entries(byService).sort((a, b) => b[1] - a[1]).slice(0, 10);

  const activeScanCount = running.reduce((n, t) => n + (t.lastStatus !== 'STOPPED' ? 1 : 0), 0);

  return (
    <ContentLayout
      header={
        <div className="soc-hero">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 24, flexWrap: 'wrap' }}>
            <div>
              <h1>Security Operations Center</h1>
              <div className="soc-hero-sub">
                Prowler · Amazon Bedrock · Amazon DevOps Agent · Account <span style={{ color: 'var(--soc-accent)', fontFamily: 'JetBrains Mono, monospace' }}>{accountId}</span> · Region <span style={{ color: 'var(--soc-accent)', fontFamily: 'JetBrains Mono, monospace' }}>{region}</span>
              </div>
              <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
                {activeScanCount > 0 ? (
                  <><span className="soc-pulse" /><span style={{ color: COLOR.fgMuted, fontSize: 13 }}>Scanner active · {activeScanCount} task(s)</span></>
                ) : loading ? (
                  <span style={{ color: COLOR.fgMuted, fontSize: 13 }}>Loading data…</span>
                ) : (
                  <><span className="soc-pulse soc-pulse--ok" /><span style={{ color: COLOR.fgMuted, fontSize: 13 }}>System idle · {scans.length} scan(s) in history</span></>
                )}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              <Button iconName="refresh" onClick={load} loading={loading} ariaLabel="Refresh dashboard data">Refresh</Button>
              <Button variant="primary" onClick={startScan} loading={starting || activeScanCount > 0} disabled={activeScanCount > 0}>
                {activeScanCount > 0 ? `Scan running (${activeScanCount})` : 'Run scan now'}
              </Button>
            </div>
          </div>

          <div className="soc-grid-5" style={{ marginTop: 24 }}>
            <div className="soc-kpi">
              <div className="soc-kpi-label">Total findings</div>
              <div className="soc-kpi-value">{loading ? '…' : total}</div>
              <div className="soc-kpi-hint">scanned</div>
            </div>
            <div className="soc-kpi soc-kpi--critical">
              <div className="soc-kpi-label">Critical</div>
              <div className="soc-kpi-value">{loading ? '…' : criticalTotal}</div>
              <div className="soc-kpi-hint">{criticalFail} failing · {criticalTotal - criticalFail} passing</div>
            </div>
            <div className="soc-kpi soc-kpi--high">
              <div className="soc-kpi-label">High</div>
              <div className="soc-kpi-value">{loading ? '…' : highTotal}</div>
              <div className="soc-kpi-hint">{highFail} failing · {highTotal - highFail} passing</div>
            </div>
            <div className="soc-kpi soc-kpi--ok">
              <div className="soc-kpi-label">Compliance</div>
              <div className="soc-kpi-value">{loading ? '…' : `${compliancePct}%`}</div>
              <div className="soc-kpi-hint">
                {passing}/{denominator} passing
                {total > denominator && ` · ${total - denominator} suppressed`}
              </div>
            </div>
            <div className="soc-kpi soc-kpi--accent">
              <div className="soc-kpi-label">AI Generated Insights</div>
              <div className="soc-kpi-value">{loading ? '…' : withInsights}</div>
              <div className="soc-kpi-hint">Bedrock playbooks</div>
            </div>
          </div>
        </div>
      }
    >
      <SpaceBetween size="l">
        {error && (
          <Alert type="error" dismissible onDismiss={() => setError(null)} action={<Button onClick={load}>Retry</Button>}>
            {error}
          </Alert>
        )}
        {message && <Alert type="info" dismissible onDismiss={() => setMessage(null)}>{message}</Alert>}

        {/* Gauge + Donut */}
        <div className="soc-grid-2">
          <Container header={<Header variant="h2" description="Pass rate across all Prowler checks">Compliance score</Header>}>
            <Gauge pct={compliancePct} label="Compliance" size={240} />
          </Container>
          <Container header={<Header variant="h2" description="All findings by severity bucket">Severity distribution</Header>}>
            <Donut data={donutData} size={220} />
          </Container>
        </div>

        {/* Triage shortcuts */}
        <Container header={<Header variant="h2" description="One click to the findings that matter right now. FAIL-only tiles are highlighted.">Triage shortcuts</Header>}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <button className="soc-tile" onClick={() => navigate('/findings?severity=CRITICAL&status=FAIL')} style={{ borderColor: 'var(--soc-critical)', gap: 16, minWidth: 190 }}>
              <span style={{ color: 'var(--soc-critical)', fontWeight: 700, fontSize: 13 }}>CRITICAL · FAIL</span>
              <span style={{ color: COLOR.fg, fontWeight: 700, fontSize: 22, fontVariantNumeric: 'tabular-nums' }}>{criticalFail}</span>
            </button>
            <button className="soc-tile" onClick={() => navigate('/findings?severity=CRITICAL')} style={{ gap: 16, minWidth: 190 }}>
              <span style={{ color: 'var(--soc-critical)', fontWeight: 600, fontSize: 13 }}>CRITICAL (all)</span>
              <span style={{ color: COLOR.fg, fontWeight: 700, fontSize: 22, fontVariantNumeric: 'tabular-nums' }}>{criticalTotal}</span>
            </button>
            <button className="soc-tile" onClick={() => navigate('/findings?severity=HIGH&status=FAIL')} style={{ borderColor: 'var(--soc-high)', gap: 16, minWidth: 190 }}>
              <span style={{ color: 'var(--soc-high)', fontWeight: 700, fontSize: 13 }}>HIGH · FAIL</span>
              <span style={{ color: COLOR.fg, fontWeight: 700, fontSize: 22, fontVariantNumeric: 'tabular-nums' }}>{highFail}</span>
            </button>
            <button className="soc-tile" onClick={() => navigate('/findings?severity=HIGH')} style={{ gap: 16, minWidth: 190 }}>
              <span style={{ color: 'var(--soc-high)', fontWeight: 600, fontSize: 13 }}>HIGH (all)</span>
              <span style={{ color: COLOR.fg, fontWeight: 700, fontSize: 22, fontVariantNumeric: 'tabular-nums' }}>{highTotal}</span>
            </button>
            <button className="soc-tile" onClick={() => navigate('/findings?status=FAIL')} style={{ gap: 16, minWidth: 190 }}>
              <span style={{ color: COLOR.fgMuted, fontSize: 13 }}>All failing</span>
              <span style={{ color: COLOR.fg, fontWeight: 700, fontSize: 22, fontVariantNumeric: 'tabular-nums' }}>{failing}</span>
            </button>
            <button className="soc-tile" onClick={() => navigate('/findings')} style={{ gap: 16, minWidth: 190 }}>
              <span style={{ color: COLOR.fgMuted, fontSize: 13 }}>All findings</span>
              <span style={{ color: COLOR.fg, fontWeight: 700, fontSize: 22, fontVariantNumeric: 'tabular-nums' }}>{total}</span>
            </button>
          </div>
        </Container>

        {/* Top priority findings */}
        <Container
          header={
            <Header
              variant="h2"
              description={
                criticalFail === 0 && criticalTotal > 0
                  ? `All ${criticalTotal} CRITICAL checks are passing ✓ — listing HIGH/MEDIUM failing findings below.`
                  : 'The fastest way to demo AI Generated Insights and DevOps Agent investigation'
              }
            >
              Top priority findings
            </Header>
          }
        >
          {topCritical.length === 0 ? (
            <Box color="text-status-inactive" textAlign="center">No failing findings yet. Run a scan.</Box>
          ) : (
            <SpaceBetween size="xs">
              {topCritical.map((f) => (
                <button key={f.finding_uid} className="soc-tile" onClick={() => navigate(`/findings/${encodeURIComponent(f.finding_uid)}`)} style={{ width: '100%', textAlign: 'left' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 14, flex: 1, minWidth: 0 }}>
                    <span className={`soc-severity-chip soc-severity-chip--${f.severity}`}>{f.severity}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ color: COLOR.fg, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {f.check_title || f.check_id}
                      </div>
                      <div style={{ color: COLOR.fgDim, fontSize: 11, fontFamily: 'JetBrains Mono, monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {f.resource_uid}
                      </div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 10, alignItems: 'center', color: COLOR.fgMuted, fontSize: 11 }}>
                    <span>{f.service_name}</span>
                    {f.remediation_s3_key && <span style={{ color: 'var(--soc-accent)' }}>Insights ready →</span>}
                  </div>
                </button>
              ))}
            </SpaceBetween>
          )}
        </Container>

        {/* Bar + heatmap */}
        <div className="soc-grid-2">
          <Container header={<Header variant="h2" description="Where the failing findings concentrate">Failing by service (top 10)</Header>}>
            {barEntries.length === 0 ? (
              <Box color="text-status-inactive" textAlign="center">No failing findings.</Box>
            ) : <BarChart entries={barEntries} />}
          </Container>
          <Container header={<Header variant="h2" description="Every AWS service grouped by failure count">Service heatmap</Header>}>
            {Object.keys(byService).length === 0 ? (
              <Box color="text-status-inactive" textAlign="center">No failing findings.</Box>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 8 }}>
                {Object.entries(byService).sort((a, b) => b[1] - a[1]).map(([service, count]) => {
                  const color = count >= 5 ? 'var(--soc-critical)' : count >= 3 ? 'var(--soc-high)' : 'var(--soc-accent)';
                  return (
                    <button
                      key={service}
                      className="soc-service-cell"
                      onClick={() => navigate(`/findings?service=${encodeURIComponent(service)}&status=FAIL`)}
                      title={`Show all FAIL findings in ${service}`}
                    >
                      <div className="soc-service-name">{service}</div>
                      <div className="soc-service-count" style={{ color }}>{count}</div>
                    </button>
                  );
                })}
              </div>
            )}
          </Container>
        </div>

        {/* Active scans */}
        <Container
          header={
            <Header
              variant="h2"
              description="Live ECS Fargate tasks · auto-refresh 10s"
              actions={<Button iconName="refresh" onClick={refreshRunning} ariaLabel="Refresh active scans">Refresh</Button>}
            >
              Active scans {activeScanCount > 0 && <span style={{ color: 'var(--soc-critical)', fontWeight: 700 }}>· {activeScanCount} live</span>}
            </Header>
          }
        >
          {running.length === 0 ? (
            <Box textAlign="center" padding={{ vertical: 's' }}>
              <Box color="text-status-inactive">No scans running.</Box>
              <Box variant="small" color="text-status-inactive">
                Click <strong>Run scan now</strong> to start a Prowler Fargate task. Typical run: 5–10 min.
              </Box>
            </Box>
          ) : (
            <SpaceBetween size="s">
              {scans.length === 0 && activeScanCount > 0 && (
                <Alert type="info" header="First scan is running">
                  Your first Prowler scan was started automatically at deploy time so the dashboard isn't empty
                  when you open it. Scan duration depends on account size: typically 3–10 min, larger accounts
                  can take 20+ min. Findings appear below as soon as ingest completes.
                </Alert>
              )}
              {running.map((t) => {
                const ok = t.lastStatus === 'STOPPED' && (!t.stoppedReason || t.stoppedReason.toLowerCase().includes('essential container'));
                const color = t.lastStatus === 'RUNNING' ? 'var(--soc-accent)' : t.lastStatus === 'STOPPED' ? (ok ? 'var(--soc-ok)' : 'var(--soc-critical)') : 'var(--soc-medium)';
                const progress = progressByTask[t.taskArn] || null;
                const isLive = t.lastStatus === 'RUNNING' || t.lastStatus === 'PENDING' || t.lastStatus === 'PROVISIONING';
                return (
                  <div key={t.taskArn} style={{ padding: '10px 14px', border: '1px solid var(--soc-border)', borderRadius: 6, background: 'var(--soc-bg)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <span style={{ display: 'inline-block', width: 10, height: 10, background: color, borderRadius: '50%', boxShadow: `0 0 10px ${color}` }} />
                      <code style={{ color: COLOR.fg, fontSize: 12 }}>{shortTaskId(t.taskArn)}</code>
                      <span style={{ color, fontWeight: 600, fontSize: 12 }}>{t.lastStatus}</span>
                      {t.lastStatus === 'PROVISIONING' && <span style={{ color: COLOR.fgDim, fontSize: 11 }}>pulling image</span>}
                      {t.lastStatus === 'PENDING' && <span style={{ color: COLOR.fgDim, fontSize: 11 }}>starting container</span>}
                      {t.lastStatus === 'STOPPED' && ok && <span style={{ color: COLOR.fgDim, fontSize: 11 }}>finished</span>}
                      {t.lastStatus === 'STOPPED' && !ok && <span style={{ color: COLOR.fgDim, fontSize: 11 }}>{t.stoppedReason}</span>}
                      {t.createdAt && <span style={{ color: COLOR.fgDim, fontSize: 11, marginLeft: 'auto' }}>started {new Date(t.createdAt).toLocaleTimeString()}</span>}
                    </div>
                    {isLive && (
                      <Box margin={{ top: 'xs' }}>
                        <LiveRegion>
                          <ProgressBar
                            status="in-progress"
                            value={progress?.percent ?? 0}
                            label={progress?.label ?? 'Warming up Fargate task…'}
                            description={
                              progress?.current && progress?.total
                                ? `Check ${progress.current} of ${progress.total}`
                                : t.lastStatus === 'PROVISIONING'
                                ? 'Pulling Prowler image from ECR'
                                : t.lastStatus === 'PENDING'
                                ? 'Allocating compute capacity'
                                : 'Prowler is enumerating AWS services'
                            }
                            additionalInfo={progress?.line ? <code style={{ fontSize: 11, color: COLOR.fgDim }}>{progress.line.slice(0, 140)}</code> : undefined}
                          />
                        </LiveRegion>
                      </Box>
                    )}
                  </div>
                );
              })}
            </SpaceBetween>
          )}
        </Container>

        {/* Scan history */}
        <Container header={<Header variant="h2" description="Historical Prowler scan runs">Scan history</Header>}>
          {scans.length === 0 ? (
            <StatusIndicator type="info">No scans yet. Click <em>Run scan now</em> above.</StatusIndicator>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {scans.slice(0, 10).map((sc) => (
                <button key={sc.scan_id} className="soc-tile" onClick={() => navigate(`/findings?scan=${sc.scan_id}`)} style={{ width: '100%' }}>
                  <code style={{ color: 'var(--soc-accent)', fontSize: 12 }}>{sc.scan_id}</code>
                  <span style={{ color: COLOR.fgDim, fontSize: 11 }}>last seen {sc.last_seen_at}</span>
                </button>
              ))}
            </div>
          )}
        </Container>
      </SpaceBetween>
    </ContentLayout>
  );
}
