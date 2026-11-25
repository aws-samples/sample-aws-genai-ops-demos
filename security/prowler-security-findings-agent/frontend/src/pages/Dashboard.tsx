import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  ColumnLayout,
  Container,
  ContentLayout,
  Header,
  Link,
  LiveRegion,
  ProgressBar,
  SpaceBetween,
  StatusIndicator,
  StatusIndicatorProps,
} from '@cloudscape-design/components';
import {
  getRunningScanLogs,
  listAllFindings,
  listRunningScans,
  listScans,
  runScan,
  Finding,
  RunningTask,
  ScanProgress,
} from '../api';
import { severityRank } from '../theme';
import { SEVERITIES } from '../constants';
import { SeverityBadge, severityLabel } from '../ui';

const SEVERITY_FILL: Record<string, string> = {
  CRITICAL: 'var(--soc-critical)',
  HIGH: 'var(--soc-high)',
  MEDIUM: 'var(--soc-medium)',
  LOW: 'var(--soc-low)',
  INFO: 'var(--soc-info)',
};

const SERVICE_ROWS = 5;

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

/** "20260509T060142Z" → Date, falling back to the raw last-seen timestamp. */
function scanDate(scanId: string, lastSeen?: string): Date | null {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(scanId);
  if (m) return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]));
  const d = lastSeen ? new Date(lastSeen) : null;
  return d && !Number.isNaN(d.getTime()) ? d : null;
}

function relative(date: Date | null): string {
  if (!date) return '';
  const diff = Math.max(0, Date.now() - date.getTime());
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.floor(h / 24);
  return d === 1 ? 'yesterday' : `${d} days ago`;
}

function pct(part: number, whole: number): string {
  if (!whole) return '—';
  return `${Math.round((part / whole) * 100)}%`;
}

function taskStatus(t: RunningTask): { type: StatusIndicatorProps.Type; label: string; detail?: string } {
  const ok = t.lastStatus === 'STOPPED' && (!t.stoppedReason || t.stoppedReason.toLowerCase().includes('essential container'));
  switch (t.lastStatus) {
    case 'RUNNING': return { type: 'in-progress', label: 'Running' };
    case 'PENDING': return { type: 'pending', label: 'Pending', detail: 'starting container' };
    case 'PROVISIONING': return { type: 'pending', label: 'Provisioning', detail: 'pulling image' };
    case 'STOPPED': return ok ? { type: 'success', label: 'Finished' } : { type: 'error', label: 'Stopped', detail: t.stoppedReason };
    default: return { type: 'info', label: t.lastStatus };
  }
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
  const [showAllServices, setShowAllServices] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [res, sc, rn] = await Promise.all([
        listAllFindings(),
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
      listAllFindings().then((r) => setFindings(r.items || [])).catch(() => {});
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
  const failingBySeverity = useMemo(
    () => countBy(findings.filter((f) => f.status === 'FAIL'), 'severity'),
    [findings],
  );
  const byService = useMemo(
    () => countBy(unsuppressed.filter((f) => f.status === 'FAIL'), 'service_name'),
    [unsuppressed],
  );
  const byStatus = useMemo(() => countBy(unsuppressed, 'status'), [unsuppressed]);

  const failing = byStatus.FAIL || 0;
  const passing = byStatus.PASS || 0;
  const denominator = unsuppressed.length;
  const compliancePct = denominator === 0 ? 0 : Math.round((passing / denominator) * 100);

  const criticalFail = failingBySeverity.CRITICAL || 0;
  const criticalTotal = bySeverity.CRITICAL || 0;
  const highFail = failingBySeverity.HIGH || 0;
  const highTotal = bySeverity.HIGH || 0;
  const withInsights = useMemo(() => findings.filter((f) => Boolean(f.remediation_s3_key)).length, [findings]);
  const failingAll = useMemo(() => findings.filter((f) => f.status === 'FAIL').length, [findings]);
  const accountId = findings[0]?.account_id || '—';
  const region = findings[0]?.region || '—';

  const topCritical = useMemo(() => {
    return findings
      .filter((f) => f.status === 'FAIL')
      .sort((a, b) => severityRank(a.severity) - severityRank(b.severity))
      .slice(0, 6);
  }, [findings]);

  const services = useMemo(() => Object.entries(byService).sort((a, b) => b[1] - a[1]), [byService]);
  const serviceMax = services[0]?.[1] || 1;
  const visibleServices = showAllServices ? services : services.slice(0, SERVICE_ROWS);

  const activeScanCount = running.reduce((n, t) => n + (t.lastStatus !== 'STOPPED' ? 1 : 0), 0);
  const latestScan = scans[0];
  const latestScanDate = latestScan ? scanDate(latestScan.scan_id, latestScan.last_seen_at) : null;

  const go = (path: string) => (e: CustomEvent) => { e.preventDefault(); navigate(path); };

  const v = (n: number | string) => (loading && !findings.length ? '…' : n);

  const finding = (() => {
    if (loading && !findings.length) return 'Loading the latest Prowler results…';
    if (total === 0) return 'No findings yet. Run a scan to evaluate this account against the Prowler checks.';
    if (failing === 0) return `All ${denominator} evaluated checks are passing.`;
    const lead = criticalFail > 0
      ? `${criticalFail} critical and ${highFail} high-severity checks are failing.`
      : highFail > 0
        ? `No critical checks are failing; ${highFail} high-severity checks are.`
        : `No critical or high-severity checks are failing.`;
    const top = services[0];
    return top ? `${lead} ${top[0]} has the most failures (${top[1]} of ${failing}).` : lead;
  })();

  const scanBlock = (
    <Container
      header={
        <Header
          variant="h2"
          counter={activeScanCount > 0 ? `(${activeScanCount} live)` : undefined}
          description="Prowler runs as an ECS Fargate task. This view refreshes every 10 s while a scan is live."
        >
          Active scans
        </Header>
      }
    >
      {running.length === 0 ? (
        <div className="soc-empty">
          <strong>No scan is running.</strong>
          <span>Choose Run scan now to start a Prowler Fargate task. A typical run takes 5–10 minutes.</span>
        </div>
      ) : (
        <SpaceBetween size="s">
          {scans.length === 0 && activeScanCount > 0 && (
            <Alert type="info" header="First scan is running">
              Your first Prowler scan was started automatically at deploy time so the dashboard isn't empty
              when you open it. Scan duration depends on account size: typically 3–10 min, larger accounts
              can take 20+ min. Findings appear below as soon as ingest completes.
            </Alert>
          )}
          <div>
            {running.map((t) => {
              const s = taskStatus(t);
              const progress = progressByTask[t.taskArn] || null;
              const isLive = t.lastStatus === 'RUNNING' || t.lastStatus === 'PENDING' || t.lastStatus === 'PROVISIONING';
              const label = progress?.label
                ?? (t.lastStatus === 'PROVISIONING' ? 'Pulling Prowler image from ECR'
                : t.lastStatus === 'PENDING' ? 'Allocating compute capacity'
                : 'Warming up Fargate task…');
              const detail = progress?.current && progress?.total
                ? `Check ${progress.current} of ${progress.total}`
                : t.lastStatus === 'RUNNING'
                  ? 'Prowler is enumerating AWS services (no progress breakdown is emitted — logs show activity)'
                  : '';
              return (
                <div key={t.taskArn} className="soc-task">
                  <div className="soc-task-head">
                    <StatusIndicator type={s.type}>{s.label}</StatusIndicator>
                    <code translate="no">{shortTaskId(t.taskArn)}</code>
                    {s.detail && <Box color="text-status-inactive" fontSize="body-s">{s.detail}</Box>}
                    {t.createdAt && <span className="soc-task-time">Started {new Date(t.createdAt).toLocaleTimeString()}</span>}
                  </div>
                  {isLive && (
                    <Box margin={{ top: 'xs' }}>
                      <LiveRegion>
                        {progress?.current && progress?.total ? (
                          <ProgressBar
                            value={Math.round((progress.current / progress.total) * 100)}
                            label={label}
                            description={detail}
                          />
                        ) : (
                          <Box>
                            <Box fontWeight="bold">{label}</Box>
                            {detail && <Box color="text-status-inactive" fontSize="body-s">{detail}</Box>}
                          </Box>
                        )}
                        {progress?.line && (
                          <div className="soc-task-line"><code>{progress.line.slice(0, 140)}</code></div>
                        )}
                      </LiveRegion>
                    </Box>
                  )}
                </div>
              );
            })}
          </div>
        </SpaceBetween>
      )}
    </Container>
  );

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description={
            <SpaceBetween size="xxs">
              <span className="soc-meta">
                <span>Account <code translate="no">{accountId}</code></span>
                <span>Region <code translate="no">{region}</code></span>
                <span>
                  {activeScanCount > 0 ? (
                    <><span className="soc-pulse soc-pulse--accent" aria-hidden="true" />Scanning · {activeScanCount} task(s)</>
                  ) : loading ? (
                    <>Loading data…</>
                  ) : (
                    <><span className="soc-dot soc-dot--ok" aria-hidden="true" />Idle · {scans.length} scan(s) on record{latestScanDate ? ` · last ${relative(latestScanDate)}` : ''}</>
                  )}
                </span>
              </span>
              <span>{finding}</span>
            </SpaceBetween>
          }
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} loading={loading} ariaLabel="Refresh dashboard data">Refresh</Button>
              <Button variant="primary" onClick={startScan} loading={starting || activeScanCount > 0} disabled={activeScanCount > 0}>
                {activeScanCount > 0 ? `Scan running (${activeScanCount})` : 'Run scan now'}
              </Button>
            </SpaceBetween>
          }
        >
          Security posture
        </Header>
      }
    >
      <SpaceBetween size="l">
        {error && (
          <Alert type="error" dismissible onDismiss={() => setError(null)} action={<Button onClick={load}>Retry</Button>}>
            {error}
          </Alert>
        )}
        {message && <Alert type="info" dismissible onDismiss={() => setMessage(null)}>{message}</Alert>}

        <Container>
          <ColumnLayout columns={5} variant="text-grid" minColumnWidth={150}>
            <div className="soc-kpi soc-kpi--lead">
              <Box variant="awsui-key-label">Compliance</Box>
              <div className="soc-kpi-value">{v(`${compliancePct}%`)}</div>
              <div className="soc-kpi-hint">
                {passing}/{denominator} passing
                {total > denominator && ` · ${total - denominator} suppressed`}
              </div>
            </div>
            <div className="soc-kpi soc-kpi--critical">
              <span className="soc-kpi-label">Critical</span>
              <div className="soc-kpi-value">{v(criticalTotal)}</div>
              <div className="soc-kpi-hint">{criticalFail} failing · {criticalTotal - criticalFail} passing</div>
            </div>
            <div className="soc-kpi soc-kpi--high">
              <span className="soc-kpi-label">High</span>
              <div className="soc-kpi-value">{v(highTotal)}</div>
              <div className="soc-kpi-hint">{highFail} failing · {highTotal - highFail} passing</div>
            </div>
            <div className="soc-kpi">
              <Box variant="awsui-key-label">Total findings</Box>
              <div className="soc-kpi-value">{v(total)}</div>
              <div className="soc-kpi-hint">scanned</div>
            </div>
            <div className="soc-kpi">
              <Box variant="awsui-key-label">AI Generated Insights</Box>
              <div className="soc-kpi-value">{v(withInsights)}</div>
              <div className="soc-kpi-hint">Bedrock playbooks</div>
            </div>
          </ColumnLayout>
        </Container>

        {activeScanCount > 0 && scanBlock}

        <div className="soc-row soc-row--7-5 soc-row--stretch">
          <div className="soc-fill">
            <Container
              header={
                <Header
                  variant="h2"
                  description="Failing checks by severity. Select a count to open those findings."
                  actions={
                    <SpaceBetween direction="horizontal" size="s">
                      <Link href="/findings?status=FAIL" onFollow={go('/findings?status=FAIL')}>All failing ({failing})</Link>
                      <Link href="/findings" onFollow={go('/findings')}>All findings ({total})</Link>
                    </SpaceBetween>
                  }
                >
                  Triage by severity
                </Header>
              }
            >
              <SpaceBetween size="m">
                <div
                  className="soc-ladder"
                  role="img"
                  aria-label={`Failing checks by severity: ${SEVERITIES.map((s) => `${severityLabel(s)} ${failingBySeverity[s] || 0}`).join(', ')}`}
                >
                  {SEVERITIES.filter((s) => (failingBySeverity[s] || 0) > 0).map((s) => (
                    <span key={s} style={{ flexGrow: failingBySeverity[s], background: SEVERITY_FILL[s] }} title={`${severityLabel(s)}: ${failingBySeverity[s]} failing`} />
                  ))}
                </div>
                <table className="soc-legend">
                  <thead>
                    <tr>
                      <th scope="col">Severity</th>
                      <th scope="col">Failing</th>
                      <th scope="col">All findings</th>
                      <th scope="col">Share</th>
                    </tr>
                  </thead>
                  <tbody>
                    {SEVERITIES.map((s) => {
                      const f = failingBySeverity[s] || 0;
                      const all = bySeverity[s] || 0;
                      return (
                        <tr key={s}>
                          <td><SeverityBadge severity={s} /></td>
                          <td>
                            {f > 0 ? (
                              <Link href={`/findings?severity=${s}&status=FAIL`} onFollow={go(`/findings?severity=${s}&status=FAIL`)} ariaLabel={`${f} failing ${severityLabel(s)} findings`}>{f}</Link>
                            ) : <span className="soc-numlink--zero">0</span>}
                          </td>
                          <td>
                            {all > 0 ? (
                              <Link variant="secondary" href={`/findings?severity=${s}`} onFollow={go(`/findings?severity=${s}`)} ariaLabel={`All ${all} ${severityLabel(s)} findings`}>{all}</Link>
                            ) : <span className="soc-numlink--zero">0</span>}
                          </td>
                          <td className="soc-legend-share">{pct(f, failingAll)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </SpaceBetween>
            </Container>
          </div>

          <div className="soc-fill">
            <Container
              header={
                <Header
                  variant="h2"
                  counter={services.length ? `(${services.length})` : undefined}
                  description="Where the failing findings concentrate"
                >
                  Failing by service
                </Header>
              }
              footer={services.length > SERVICE_ROWS ? (
                <Box textAlign="center">
                  <Link onFollow={(e) => { e.preventDefault(); setShowAllServices((x) => !x); }} href="#services">
                    {showAllServices ? 'Show top services' : `Show all ${services.length} services`}
                  </Link>
                </Box>
              ) : undefined}
            >
              {services.length === 0 ? (
                <div className="soc-empty"><strong>No failing findings.</strong><span>Every evaluated service passes its checks.</span></div>
              ) : (
                <ul className="soc-bars" aria-label="Failing findings by service" id="services">
                  {visibleServices.map(([service, count]) => (
                    <li key={service}>
                      <button
                        type="button"
                        className="soc-bar-button"
                        onClick={() => navigate(`/findings?service=${encodeURIComponent(service)}&status=FAIL`)}
                        aria-label={`${service}: ${count} failing. Show these findings`}
                      >
                        <span className="soc-bar-name">{service}</span>
                        <span className="soc-bar-count">{count}</span>
                        <span className="soc-bar-track" aria-hidden="true">
                          <span className="soc-bar-fill" style={{ display: 'block', transform: `scaleX(${count / serviceMax})` }} />
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Container>
          </div>
        </div>

        <Container
          header={
            <Header
              variant="h2"
              description={
                criticalFail === 0 && criticalTotal > 0
                  ? `All ${criticalTotal} critical checks are passing. Listing the highest-severity failing findings instead.`
                  : 'Open a finding to read its AI Generated Insights and dispatch a DevOps Agent investigation.'
              }
              actions={failing > 0 ? <Link href="/findings?status=FAIL" onFollow={go('/findings?status=FAIL')}>View all failing</Link> : undefined}
            >
              Top priority findings
            </Header>
          }
        >
          {topCritical.length === 0 ? (
            <div className="soc-empty">
              <strong>No failing findings yet.</strong>
              <span>Run a scan to evaluate this account. Failing checks will be listed here by severity.</span>
            </div>
          ) : (
            <ul className="soc-list">
              {topCritical.map((f) => (
                <li key={f.finding_uid}>
                  <button
                    type="button"
                    className="soc-row-button"
                    onClick={() => navigate(`/findings/${encodeURIComponent(f.finding_uid)}`)}
                  >
                    <SeverityBadge severity={f.severity} />
                    <span className="soc-row-main">
                      <span className="soc-row-title" style={{ display: 'block' }}>{f.check_title || f.check_id}</span>
                      <span className="soc-row-sub" style={{ display: 'block' }}><code translate="no">{f.resource_uid}</code></span>
                    </span>
                    <span className="soc-row-end">
                      <span>{f.service_name}</span>
                      {f.remediation_s3_key && <StatusIndicator type="success">Insights ready</StatusIndicator>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Container>

        {activeScanCount === 0 && scanBlock}

        <Container
          header={
            <Header variant="h2" counter={scans.length ? `(${scans.length})` : undefined} description="Historical Prowler scan runs. Select one to see its findings.">
              Scan history
            </Header>
          }
        >
          {scans.length === 0 ? (
            <StatusIndicator type="info">No scans yet. Choose Run scan now above.</StatusIndicator>
          ) : (
            <ul className="soc-list">
              {scans.slice(0, 10).map((sc) => {
                const d = scanDate(sc.scan_id, sc.last_seen_at);
                return (
                  <li key={sc.scan_id}>
                    <button type="button" className="soc-row-button" style={{ minHeight: 44 }} onClick={() => navigate(`/findings?scan=${sc.scan_id}`)}>
                      <span className="soc-row-main">
                        <span className="soc-row-title" style={{ display: 'block', fontWeight: 400 }}>
                          {d ? d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : sc.scan_id}
                        </span>
                      </span>
                      <span className="soc-row-end">
                        <span>{relative(d)}</span>
                        <code translate="no">{sc.scan_id}</code>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Container>
      </SpaceBetween>
    </ContentLayout>
  );
}
