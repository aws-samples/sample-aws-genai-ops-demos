import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  ContentLayout,
  Container,
  ExpandableSection,
  Header,
  SpaceBetween,
  Spinner,
  Tabs,
} from '@cloudscape-design/components';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  AgentJournalRecord,
  Finding,
  InvestigationState,
  generateInsights,
  getFinding,
  getInvestigation,
  investigateFinding,
} from '../api';

import { COLOR } from '../theme';
import { badgeFromHistory } from '../status-history';

function investigationBadge(status: InvestigationState['status']) {
  const map: Record<string, { color: string; text: string }> = {
    idle:           { color: COLOR.fgDim,    text: 'Not yet investigated' },
    completed:      { color: COLOR.ok,       text: 'Investigation completed' },
    in_progress:    { color: COLOR.accent,   text: 'Agent investigating…' },
    pending:        { color: COLOR.medium,   text: 'Dispatched · waiting' },
    not_configured: { color: COLOR.fgDim,    text: 'DevOps Agent not configured' },
    error:          { color: COLOR.critical, text: 'API error' },
  };
  const m = map[status] || { color: COLOR.fgDim, text: status };
  const pulseClass = status === 'in_progress' ? 'soc-pulse soc-pulse--accent'
                   : status === 'pending' ? 'soc-pulse'
                   : status === 'completed' ? 'soc-pulse soc-pulse--ok'
                   : '';
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      {pulseClass && (
        <span
          aria-hidden="true"
          className={pulseClass}
          style={{ background: m.color, boxShadow: `0 0 12px ${m.color}` }}
        />
      )}
      <span role="status" aria-live="polite" style={{ color: m.color, fontWeight: 600 }}>{m.text}</span>
    </span>
  );
}

/**
 * Render a single DevOps Agent journal record into something a human can read.
 * Records are nested JSON blobs that contain reasoning, tool calls, tool
 * results, and metadata; we surface the parts that matter and hide telemetry.
 */
interface ParsedEntry {
  kind: 'reasoning' | 'tool-call' | 'tool-result' | 'text' | 'user-prompt' | 'skip';
  title: string;
  subtitle?: string;
  body?: string;
  status?: 'success' | 'error';
}

function parseJournalRecord(r: AgentJournalRecord): ParsedEntry[] {
  if (!r.content) return [];
  let root: any;
  try {
    root = JSON.parse(r.content);
  } catch {
    return [{ kind: 'text', title: 'event', body: r.content }];
  }
  // Metadata / context-window noise — discard entirely.
  if (root?.metadata && root?.data?.context_window) return [];

  const role = root?.role;
  const parts: any[] = Array.isArray(root?.content) ? root.content : [];
  const out: ParsedEntry[] = [];

  for (const part of parts) {
    if (part?.type === 'thinking' && part?.thinking) {
      out.push({ kind: 'reasoning', title: 'Agent reasoning', body: part.thinking });
      continue;
    }
    if (part?.type === 'tool_use') {
      const name = part?.tool_name || 'tool';
      const svc = part?.input?.service_name || part?.input?.file_path;
      const op = part?.input?.operation_name;
      const subtitle = svc && op ? `${svc}.${op}` : svc || op || '';
      out.push({
        kind: 'tool-call',
        title: `Calling ${name}`,
        subtitle,
        body: JSON.stringify(part?.input ?? {}, null, 2),
      });
      continue;
    }
    if (part?.type === 'tool_result') {
      const inner = Array.isArray(part?.content) ? part.content : [];
      const text = inner.map((i: any) => i?.text || '').join('\n').trim();
      const status = part?.status === 'error' ? 'error' : 'success';
      out.push({
        kind: 'tool-result',
        title: status === 'error' ? 'Tool error' : 'Tool result',
        body: text,
        status,
      });
      continue;
    }
    if (part?.type === 'text' && part?.text) {
      if (role === 'user') {
        out.push({ kind: 'user-prompt', title: 'Incident briefing', body: part.text });
      } else {
        out.push({ kind: 'text', title: 'Agent response', body: part.text });
      }
    }
  }
  return out;
}

/** Try to pretty-print a tool result: JSON → indented, otherwise raw. */
function formatToolResult(raw: string): string {
  const trimmed = raw.trim();
  // The DevOps Agent wraps API responses in '{"<accountId>": <payload>}' —
  // unwrap the single-key outer object so the body is readable.
  try {
    const parsed = JSON.parse(trimmed);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const keys = Object.keys(parsed);
      if (keys.length === 1 && /^\d{12}$/.test(keys[0])) {
        return JSON.stringify(parsed[keys[0]], null, 2);
      }
    }
    return JSON.stringify(parsed, null, 2);
  } catch {
    return trimmed;
  }
}

function JournalEntry({ entry, defaultExpanded }: { entry: ParsedEntry; defaultExpanded: boolean }) {
  const badgeColor =
    entry.kind === 'tool-call'     ? COLOR.accent :
    entry.kind === 'tool-result'   ? (entry.status === 'error' ? COLOR.critical : COLOR.ok) :
    entry.kind === 'reasoning'     ? COLOR.fgMuted :
    entry.kind === 'user-prompt'   ? COLOR.high :
    entry.kind === 'text'          ? COLOR.ok :
    COLOR.fgDim;

  const body = entry.body || '';
  // Reasoning and user-prompt default collapsed regardless of length — they
  // bury the interesting parts (tool calls and final agent response) when
  // expanded inline.
  const alwaysCollapsed = entry.kind === 'reasoning' || entry.kind === 'user-prompt';
  const isLong = body.length > 600;
  const showCollapsed = alwaysCollapsed || isLong;

  const renderedBody = entry.kind === 'text' ? (
    <div className="soc-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
    </div>
  ) : entry.kind === 'tool-call' ? (
    <pre className="soc-code-block" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>{body}</pre>
  ) : entry.kind === 'tool-result' ? (
    <pre className="soc-code-block" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0, fontSize: 11.5 }}>{formatToolResult(body)}</pre>
  ) : (
    <div style={{
      fontSize: 12,
      lineHeight: 1.5,
      whiteSpace: 'pre-wrap',
      overflowWrap: 'break-word',
      wordBreak: 'break-word',
      color: COLOR.fg,
    }}>{body}</div>
  );

  const headerLabel =
    entry.kind === 'reasoning' ? 'Show reasoning' :
    entry.kind === 'user-prompt' ? 'Show briefing' :
    entry.kind === 'tool-result' ? 'Show result' :
    'Show details';

  return (
    <div style={{
      padding: '12px 14px',
      borderLeft: `3px solid ${badgeColor}`,
      background: 'rgba(0,0,0,0.04)',
      borderRadius: 4,
      marginBottom: 8,
      minWidth: 0,
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        marginBottom: entry.body ? 8 : 0,
      }}>
        <span style={{ color: badgeColor, fontWeight: 600, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          {entry.title}
        </span>
        {entry.subtitle && (
          <code style={{ color: COLOR.fgMuted, fontSize: 11 }}>{entry.subtitle}</code>
        )}
      </div>
      {entry.body && (
        showCollapsed ? (
          <ExpandableSection
            variant="footer"
            defaultExpanded={defaultExpanded && !alwaysCollapsed}
            headerText={`${headerLabel} (${body.length.toLocaleString()} chars)`}
          >
            <div style={{ minWidth: 0, maxWidth: '100%' }}>{renderedBody}</div>
          </ExpandableSection>
        ) : (
          <div style={{ minWidth: 0, maxWidth: '100%' }}>{renderedBody}</div>
        )
      )}
    </div>
  );
}

function AgentJournal({ records }: { records: AgentJournalRecord[] }) {
  // Journal order on the wire is *most recent first*. Flip it so the reader
  // sees the investigation chronologically: briefing → reasoning → tool
  // calls → final response.
  const entries = records.slice().reverse().flatMap(parseJournalRecord);
  if (entries.length === 0) return <Box color="text-status-inactive">No displayable records.</Box>;
  // Find the last 'text' (final agent response) and default-expand it so the
  // conclusion is visible without hunting.
  const lastTextIdx = (() => {
    for (let i = entries.length - 1; i >= 0; i--) if (entries[i].kind === 'text') return i;
    return -1;
  })();
  return (
    <div
      style={{ maxHeight: 560, overflowY: 'auto', overflowX: 'hidden', padding: 4, minWidth: 0, scrollbarGutter: 'stable' }}
      aria-label="DevOps Agent investigation journal"
    >
      {entries.map((e, i) => (
        <JournalEntry key={i} entry={e} defaultExpanded={i === lastTextIdx} />
      ))}
    </div>
  );
}

/**
 * Format the delta between `now` and `then` (epoch ms) as a compact, live
 * "12s ago" / "3m 14s ago" / "1h 2m ago". `now` is passed in so the caller
 * can drive re-renders on a tick.
 */
function formatAgo(then: number, now: number): string {
  if (!then) return '';
  const ms = Math.max(0, now - then);
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return rs > 0 ? `${m}m ${rs}s ago` : `${m}m ago`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm > 0 ? `${h}h ${rm}m ago` : `${h}h ago`;
}

/** Head-plus-tail truncation: "prowler-…-44dfca" */
function middleTruncate(s: string, head = 14, tail = 10): string {
  if (s.length <= head + tail + 1) return s;
  return `${s.slice(0, head)}…${s.slice(-tail)}`;
}

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      iconName={copied ? 'status-positive' : 'copy'}
      variant="inline-link"
      ariaLabel={copied ? 'Copied to clipboard' : (label ? `Copy ${label} to clipboard` : 'Copy value to clipboard')}
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1400);
      }}
    >
      {copied ? 'Copied' : 'Copy'}
    </Button>
  );
}

export default function FindingDetail() {
  const { findingUid } = useParams<{ findingUid: string }>();
  const navigate = useNavigate();
  const [item, setItem] = useState<Finding | null>(null);
  const [remediation, setRemediation] = useState<string>('');
  const [remediationError, setRemediationError] = useState<string | null>(null);
  const [generatingInsights, setGeneratingInsights] = useState<boolean>(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [investigation, setInvestigation] = useState<InvestigationState | null>(null);
  const [investigationError, setInvestigationError] = useState<string | null>(null);
  const [dispatching, setDispatching] = useState(false);
  const [dispatchMessage, setDispatchMessage] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  // Track the last dispatch timestamp so we can ignore stale 'idle' polls
  // during the ~30-60s window before the task shows up in the backlog.
  const lastDispatchAtRef = useRef<number>(0);
  // State mirror of the ref so the "Xs ago" pill re-renders every tick
  // while an investigation is in flight.
  const [dispatchedAt, setDispatchedAt] = useState<number>(0);
  const [now, setNow] = useState<number>(() => Date.now());

  const refreshInvestigation = useCallback(() => {
    if (!findingUid) return;
    getInvestigation(findingUid)
      .then((s) => {
        const sinceDispatch = Date.now() - lastDispatchAtRef.current;
        if (s.status === 'idle' && sinceDispatch < 90000) {
          // Agent hasn't picked up the task yet — don't drop the optimistic
          // 'pending' badge onto 'Not yet investigated'.
          setInvestigation((prev) => prev ?? { ...s, status: 'pending' });
        } else {
          setInvestigation(s);
        }
        setInvestigationError(null);
      })
      .catch((e) => setInvestigationError(e?.message || 'Failed to fetch investigation'));
  }, [findingUid]);

  useEffect(() => {
    if (!findingUid) return;
    setLoading(true);
    setError(null);
    getFinding(findingUid)
      .then((f) => {
        setItem(f);
        // Markdown is now inlined in the response by the dashboard-api Lambda,
        // so no second cross-origin fetch to S3 is needed.
        if (f.remediation_markdown) {
          setRemediation(f.remediation_markdown);
          setRemediationError(null);
        } else if (f.remediation_s3_key) {
          setRemediationError('Markdown was generated but the API could not read it back from S3.');
        }
      })
      .catch((e) => setError(e?.message || 'Failed to load finding'))
      .finally(() => setLoading(false));
    refreshInvestigation();
  }, [findingUid, refreshInvestigation]);

  useEffect(() => {
    if (!investigation) return;
    const inFlight = investigation.status === 'pending' || investigation.status === 'in_progress';
    if (!inFlight) {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    if (pollRef.current) return;
    // Adaptive polling: the webhook → backlog-task hop takes 30-90 s; poll
    // every 3 s during that window so the UI feels responsive, then back off
    // to 10 s for the longer investigation phase.
    const sinceDispatch = () => Date.now() - lastDispatchAtRef.current;
    const tick = () => {
      refreshInvestigation();
      const nextDelay = sinceDispatch() < 90000 ? 3000 : 10000;
      pollRef.current = window.setTimeout(tick, nextDelay) as unknown as number;
    };
    pollRef.current = window.setTimeout(tick, 3000) as unknown as number;
    return () => {
      if (pollRef.current) { window.clearTimeout(pollRef.current); pollRef.current = null; }
    };
  }, [investigation, refreshInvestigation]);

  async function dispatchGenerateInsights() {
    if (!findingUid) return;
    setGeneratingInsights(true);
    setRemediationError(null);
    try {
      const res = await generateInsights(findingUid);
      setRemediation(res.remediation_markdown);
      setItem((prev) => prev ? { ...prev, remediation_markdown: res.remediation_markdown, remediation_s3_key: res.remediation_s3_key } : prev);
    } catch (e: any) {
      setRemediationError(e?.message || 'Failed to generate insights');
    } finally {
      setGeneratingInsights(false);
    }
  }

  async function dispatchInvestigate() {
    if (!findingUid) return;
    setDispatching(true);
    setDispatchMessage(null);
    try {
      const res = await investigateFinding(findingUid);
      setDispatchMessage(`Dispatched — incident ${res.incidentId}. Expect 30–90 s for the backlog task to appear, and 1–3 min for the full investigation.`);
      const ts = Date.now();
      lastDispatchAtRef.current = ts;
      setDispatchedAt(ts);
      setNow(ts);
      setInvestigation((prev) => ({
        incidentId: res.incidentId,
        status: 'pending',
        agentSpaceId: prev?.agentSpaceId,
        executionId: prev?.executionId,
        tasks: prev?.tasks || [],
        journal: prev?.journal || [],
      }));
      setTimeout(refreshInvestigation, 1000);
    } catch (e: any) {
      setInvestigationError(e?.message || 'Failed to dispatch investigation');
    } finally {
      setDispatching(false);
    }
  }

  // Esc → back
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') navigate('/findings'); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [navigate]);

  // 1Hz "wall clock" used by the "Xs ago" pill next to the dispatched state.
  // Only ticks while there's an in-flight investigation so the tab is cheap
  // to leave open.
  const inFlight = investigation?.status === 'pending' || investigation?.status === 'in_progress';
  useEffect(() => {
    if (!inFlight || !dispatchedAt) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [inFlight, dispatchedAt]);

  if (loading) return <Box padding="xl" textAlign="center"><Spinner size="large" /></Box>;
  if (error) return <Alert type="error" action={<Button onClick={() => navigate('/findings')}>Back</Button>}>{error}</Alert>;
  if (!item) return null;

  const raw = item.raw;
  const sevColor: string = (() => {
    switch (item.severity) {
      case 'CRITICAL': return COLOR.critical;
      case 'HIGH':     return COLOR.high;
      case 'MEDIUM':   return COLOR.medium;
      case 'LOW':      return COLOR.low;
      case 'INFO':     return COLOR.info;
      default:         return COLOR.fgMuted;
    }
  })();

  return (
    <ContentLayout
      header={
        <div className="soc-hero" style={{ borderLeft: `4px solid ${sevColor}` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 24 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
                <span className={`soc-severity-chip soc-severity-chip--${item.severity}`} style={{ fontSize: 12, padding: '4px 12px' }}>
                  {item.severity}
                </span>
                <span style={{ color: item.status === 'FAIL' ? COLOR.critical : COLOR.ok, fontWeight: 600, fontSize: 13 }}>
                  {item.status}
                </span>
                {(() => {
                  const b = badgeFromHistory(item);
                  if (b.kind === 'stable') return null;
                  return (
                    <span
                      className={`soc-history-chip soc-history-chip--${b.kind}`}
                      style={{ marginLeft: 0 }}
                      title={
                        b.kind === 'fixed' ? `Status flipped to PASS in scan ${b.since}`
                        : b.kind === 'regressed' ? `Was PASS, now ${b.wasStatus}`
                        : 'First observed in the latest scan'
                      }
                    >
                      {b.label}
                    </span>
                  );
                })()}
                <span style={{ color: COLOR.fgMuted, fontSize: 13 }}>· {item.service_name}</span>
                {item.remediation_s3_key && (
                  <span style={{ color: COLOR.accent, fontSize: 12, fontWeight: 600 }}>· Bedrock insight ready</span>
                )}
              </div>
              <h1 style={{ margin: 0, fontSize: 24, fontWeight: 700, color: COLOR.fg, letterSpacing: '-0.01em' }}>
                {item.check_title || item.check_id}
              </h1>
              <div style={{ color: COLOR.fgMuted, fontSize: 12, fontFamily: 'JetBrains Mono, monospace', marginTop: 6 }}>
                {item.check_id}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <Button onClick={() => navigate('/findings')}>← Back</Button>
              <Button
                variant="primary"
                onClick={dispatchInvestigate}
                loading={dispatching}
                disabled={investigation?.status === 'in_progress' || investigation?.status === 'pending'}
                iconName="status-positive"
              >
                {investigation?.status === 'completed' ? 'Re-investigate' : 'Investigate with DevOps Agent'}
              </Button>
            </div>
          </div>
        </div>
      }
    >
      <SpaceBetween size="l">
        {dispatchMessage && <Alert type="success" dismissible onDismiss={() => setDispatchMessage(null)}>{dispatchMessage}</Alert>}

        {/* 2-col: overview + tabs (stacks on mobile) */}
        <div className="soc-grid-sidebar">
          {/* Left sidebar: facts */}
          <Container header={<Header variant="h2">Incident facts</Header>}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div>
                <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Severity</div>
                <span className={`soc-severity-chip soc-severity-chip--${item.severity}`}>{item.severity}</span>
              </div>
              <div>
                <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Status</div>
                <div style={{ color: item.status === 'FAIL' ? COLOR.critical : COLOR.ok, fontWeight: 600 }}>{item.status}</div>
              </div>
              <div>
                <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Service</div>
                <div style={{ color: COLOR.fg }}>{item.service_name}</div>
              </div>
              <div>
                <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Resource</div>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                  <code translate="no" style={{ fontSize: 11, color: COLOR.fg, wordBreak: 'break-all', lineHeight: 1.4 }}>{item.resource_uid}</code>
                  <CopyButton text={item.resource_uid} label="resource ARN" />
                </div>
              </div>
              <div>
                <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Region / Account</div>
                <div style={{ color: COLOR.fg, fontFamily: 'JetBrains Mono, monospace', fontSize: 12 }}>{item.region} · {item.account_id}</div>
              </div>
              <div>
                <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Last seen</div>
                <div style={{ color: COLOR.fg, fontSize: 12 }}>{item.last_seen_at && new Date(item.last_seen_at).toLocaleString()}</div>
              </div>
              {item.scan_id && (
                <div>
                  <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Scan</div>
                  <code style={{ fontSize: 11, color: COLOR.accent }}>{item.scan_id}</code>
                </div>
              )}
              {(item.compliance_frameworks || []).length > 0 && (
                <div>
                  <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Compliance</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {item.compliance_frameworks!.map((f) => (
                      <span key={f} className="soc-severity-chip" style={{ background: 'rgba(79,143,255,0.15)', color: COLOR.accent, border: `1px solid ${COLOR.accent}` }}>{f}</span>
                    ))}
                  </div>
                </div>
              )}
              <div>
                <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Finding UID</div>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                  <code translate="no" style={{ fontSize: 10.5, color: COLOR.fgDim, wordBreak: 'break-all', lineHeight: 1.4 }}>{item.finding_uid}</code>
                  <CopyButton text={item.finding_uid} label="finding UID" />
                </div>
              </div>
            </div>
          </Container>

          {/* Right: tabs */}
          <div style={{ minWidth: 0 }}><Container>
            <Tabs
              tabs={[
                {
                  id: 'overview',
                  label: 'Overview',
                  content: (
                    <div style={{ padding: '12px 16px' }}>
                      <SpaceBetween size="l">
                        {/* Description */}
                        <div className="soc-markdown">
                          {item.check_description ? (
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.check_description}</ReactMarkdown>
                          ) : (
                            <Box color="text-status-inactive">No description provided.</Box>
                          )}
                        </div>

                        {item.status_extended && (
                          <div>
                            <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Status detail from Prowler</div>
                            <div className="soc-markdown">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.status_extended}</ReactMarkdown>
                            </div>
                          </div>
                        )}

                        {item.risk_details && (
                          <div>
                            <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Risk</div>
                            <div className="soc-markdown">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.risk_details}</ReactMarkdown>
                            </div>
                          </div>
                        )}

                        {item.remediation_guidance && (
                          <div style={{ padding: '12px 14px', background: 'rgba(79,143,255,0.05)', border: `1px solid ${COLOR.border}`, borderRadius: 8 }}>
                            <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Prowler remediation guidance</div>
                            <div className="soc-markdown">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.remediation_guidance}</ReactMarkdown>
                            </div>
                            {item.remediation_url && (
                              <div style={{ marginTop: 10 }}>
                                <a href={item.remediation_url} target="_blank" rel="noopener noreferrer" style={{ color: COLOR.accent, fontSize: 12 }}>
                                  Read the full Prowler documentation →
                                </a>
                              </div>
                            )}
                          </div>
                        )}

                        {item.additional_urls && item.additional_urls.length > 0 && (
                          <div>
                            <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Additional references</div>
                            <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12, color: COLOR.accent }}>
                              {item.additional_urls.map((u) => (
                                <li key={u}><a href={u} target="_blank" rel="noopener noreferrer" style={{ color: COLOR.accent }}>{u}</a></li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {((item.categories && item.categories.length > 0) || item.notes || (item.finding_types && item.finding_types.length > 0)) && (
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
                            {item.categories && item.categories.length > 0 && (
                              <div>
                                <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>Categories</div>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                  {item.categories.map((c) => (
                                    <span key={c} className="soc-severity-chip" style={{ background: 'rgba(140,100,200,0.15)', color: '#B48CE6', border: '1px solid #B48CE6' }}>{c}</span>
                                  ))}
                                </div>
                              </div>
                            )}
                            {item.notes && (
                              <div>
                                <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>Notes</div>
                                <div style={{ fontSize: 12, color: COLOR.fg }}>{item.notes}</div>
                              </div>
                            )}
                            {item.finding_types && item.finding_types.length > 0 && (
                              <div>
                                <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>Finding types</div>
                                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11, color: COLOR.fg }}>
                                  {item.finding_types.map((t) => (<li key={t}>{t}</li>))}
                                </ul>
                              </div>
                            )}
                          </div>
                        )}

                        {item.status_history && item.status_history.length > 0 && (
                          <div>
                            <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>
                              History
                              {item.first_seen_at && (
                                <span style={{ marginLeft: 8, color: COLOR.fgDim, textTransform: 'none', fontWeight: 400 }}>
                                  first seen {new Date(item.first_seen_at).toLocaleDateString()}
                                </span>
                              )}
                            </div>
                            <ol style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 4 }}>
                              {item.status_history.slice().reverse().map((h, idx) => (
                                <li
                                  key={`${h.scan_id}-${idx}`}
                                  style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 8,
                                    fontSize: 12,
                                    color: COLOR.fgMuted,
                                  }}
                                >
                                  <span
                                    style={{
                                      display: 'inline-block',
                                      width: 8,
                                      height: 8,
                                      borderRadius: '50%',
                                      background:
                                        h.status === 'FAIL' ? 'var(--soc-critical)'
                                        : h.status === 'PASS' ? 'var(--soc-ok)'
                                        : 'var(--soc-medium)',
                                    }}
                                    aria-hidden="true"
                                  />
                                  <span style={{ fontWeight: 600, color: COLOR.fg }}>{h.status}</span>
                                  <span style={{ color: COLOR.fgDim }}>·</span>
                                  <code translate="no" style={{ fontSize: 11 }}>{h.scan_id}</code>
                                  <span style={{ color: COLOR.fgDim, marginLeft: 'auto' }}>
                                    {new Date(h.last_seen_at).toLocaleString()}
                                  </span>
                                </li>
                              ))}
                            </ol>
                          </div>
                        )}

                        {item.compliance_controls && Object.keys(item.compliance_controls).length > 0 && (
                          <div>
                            <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Compliance controls</div>
                            <div style={{ display: 'grid', gap: 8 }}>
                              {Object.entries(item.compliance_controls).sort(([a],[b]) => a.localeCompare(b)).map(([fw, ids]) => (
                                <div key={fw} style={{ padding: '8px 12px', background: 'rgba(79,143,255,0.03)', border: `1px solid ${COLOR.border}`, borderRadius: 6 }}>
                                  <div style={{ fontSize: 11, fontWeight: 600, color: COLOR.accent, marginBottom: 4 }}>{fw}</div>
                                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                    {ids.map((id) => (
                                      <code key={id} style={{ fontSize: 10.5, padding: '2px 6px', background: 'rgba(79,143,255,0.1)', color: COLOR.fg, borderRadius: 3 }}>{id}</code>
                                    ))}
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </SpaceBetween>
                    </div>
                  ),
                },
                {
                  id: 'remediation',
                  label: 'Bedrock Insights',
                  content: (
                    <div style={{ padding: '4px 8px' }}>
                      {remediationError ? (
                        <Alert type="error" header="Could not load Bedrock Insights">
                          {remediationError}
                        </Alert>
                      ) : remediation ? (
                        <div className="soc-markdown">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{remediation}</ReactMarkdown>
                          <div style={{ marginTop: 24, display: 'flex', gap: 8, alignItems: 'center', color: COLOR.fgDim, fontSize: 11 }}>
                            Generated by Amazon Bedrock — always verify before applying.
                            <CopyButton text={remediation} />
                          </div>
                        </div>
                      ) : generatingInsights ? (
                        <Box textAlign="center" padding="xl">
                          <SpaceBetween size="m">
                            <Spinner size="large" />
                            <Box variant="h3">Amazon Bedrock is generating insights…</Box>
                            <Box color="text-status-inactive">Bedrock is analysing the finding and producing a tailored playbook. This usually takes 10–30 seconds.</Box>
                          </SpaceBetween>
                        </Box>
                      ) : (
                        <Box textAlign="center" padding="xl">
                          <SpaceBetween size="m">
                            <Box variant="h3">No Bedrock Insights yet</Box>
                            <Box color="text-status-inactive">
                              Generate an AI-powered playbook tailored to this finding.{' '}
                              {item.status === 'FAIL' && 'For failing checks you get a remediation playbook.'}
                              {item.status === 'PASS' && 'For passing checks you get a hardening guide.'}
                              {item.status === 'MANUAL' && 'For manual checks you get a review checklist.'}
                            </Box>
                            <Button
                              variant="primary"
                              iconName="gen-ai"
                              onClick={dispatchGenerateInsights}
                              loading={generatingInsights}
                            >
                              Generate Bedrock Insights
                            </Button>
                          </SpaceBetween>
                        </Box>
                      )}
                    </div>
                  ),
                },
                {
                  id: 'investigation',
                  label: 'DevOps Agent investigation',
                  content: (
                    <div style={{ padding: '4px 8px' }}>
                      <SpaceBetween size="m">
                        {investigationError && <Alert type="error">{investigationError}</Alert>}
                        {!investigation && <Spinner />}
                        {investigation && (
                          <>
                            <div style={{ padding: '14px 18px', background: 'rgba(79,143,255,0.05)', border: `1px solid ${COLOR.border}`, borderRadius: 8 }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
                                <div style={{ minWidth: 0 }}>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                                    {investigationBadge(investigation.status)}
                                    {inFlight && dispatchedAt > 0 && (
                                      <span
                                        style={{
                                          fontSize: 11,
                                          fontVariantNumeric: 'tabular-nums',
                                          color: COLOR.fgMuted,
                                          padding: '2px 8px',
                                          border: `1px solid ${COLOR.border}`,
                                          borderRadius: 999,
                                          background: 'rgba(79,143,255,0.06)',
                                        }}
                                        aria-live="polite"
                                      >
                                        dispatched {formatAgo(dispatchedAt, now)}
                                      </span>
                                    )}
                                  </div>
                                  <div style={{ color: COLOR.fgDim, fontSize: 11, marginTop: 6 }}>
                                    Last checked {new Date(now).toLocaleTimeString()} · polls every 3–10s while active
                                  </div>
                                  <ExpandableSection
                                    variant="footer"
                                    headerText="Technical references"
                                  >
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 11 }}>
                                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: COLOR.fgMuted, flexWrap: 'wrap' }}>
                                        <span>Incident ID:</span>
                                        <code
                                          translate="no"
                                          title={investigation.incidentId}
                                          style={{ color: COLOR.accent, fontSize: 11 }}
                                        >
                                          {middleTruncate(investigation.incidentId, 16, 10)}
                                        </code>
                                        <CopyButton text={investigation.incidentId} label="incident ID" />
                                      </div>
                                      {investigation.agentSpaceId && (
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: COLOR.fgDim }}>
                                          <span>Agent Space:</span>
                                          <code translate="no" style={{ fontSize: 11 }}>{investigation.agentSpaceId}</code>
                                          <CopyButton text={investigation.agentSpaceId} label="Agent Space ID" />
                                        </div>
                                      )}
                                    </div>
                                  </ExpandableSection>
                                </div>
                                <div style={{ display: 'flex', gap: 8, flexDirection: 'column' }}>
                                  {(() => {
                                    if (!investigation.agentSpaceId) return null;
                                    // Amazon DevOps Agent operator app is global (not per-region).
                                    const operatorBase = `https://${investigation.agentSpaceId}.aidevops.global.app.aws`;
                                    // Deep-link to the execution if we got one from the backend.
                                    const execId = investigation.executionId || investigation.tasks[0]?.executionId;
                                    return (
                                      <>
                                        <Button
                                          href={`${operatorBase}/dashboard`}
                                          iconAlign="right"
                                          iconName="external"
                                          target="_blank"
                                          variant="primary"
                                        >
                                          Open Agent Operator
                                        </Button>
                                        {execId && (
                                          <Button
                                            href={`${operatorBase}/investigation/${execId}`}
                                            iconAlign="right"
                                            iconName="external"
                                            target="_blank"
                                          >
                                            View this investigation
                                          </Button>
                                        )}
                                      </>
                                    );
                                  })()}
                                </div>
                              </div>
                            </div>

                            {investigation.status === 'pending' && investigation.tasks.length === 0 ? (
                              <Alert type="info">
                                <SpaceBetween size="xs" direction="horizontal">
                                  <Spinner />
                                  <span>
                                    Waiting for DevOps Agent to pick up the task… The webhook queue typically takes
                                    30–90 seconds to create the backlog task. Full investigation usually completes
                                    in 1–3 minutes total. This page updates automatically.
                                  </span>
                                </SpaceBetween>
                              </Alert>
                            ) : investigation.tasks.length === 0 ? (
                              <Alert type="info">
                                No investigation yet. Click <strong>Investigate with DevOps Agent</strong> above to dispatch one. The agent will inspect your AWS resources and stream its journal here.
                              </Alert>
                            ) : (
                              <>
                                <div>
                                  <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Agent tasks</div>
                                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                                    {investigation.tasks.map((t) => (
                                      <div key={t.taskId} style={{ padding: '10px 14px', background: 'rgba(255,255,255,0.02)', border: `1px solid ${COLOR.border}`, borderRadius: 6 }}>
                                        <div style={{ color: COLOR.fg, fontWeight: 600 }}>{t.title}</div>
                                        <div style={{ color: COLOR.fgDim, fontSize: 11, marginTop: 4 }}>
                                          status {t.status}{t.priority ? ` · priority ${t.priority}` : ''} · created {t.createdAt}
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                </div>

                                <div>
                                  <div style={{ color: COLOR.fgMuted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>
                                    Agent activity (most recent first)
                                  </div>
                                  {investigation.journal.length === 0 ? (
                                    <Box color="text-status-inactive">Waiting for first journal entry…</Box>
                                  ) : (
                                    <AgentJournal records={investigation.journal} />
                                  )}
                                </div>
                              </>
                            )}

                          </>
                        )}
                      </SpaceBetween>
                    </div>
                  ),
                },
                {
                  id: 'raw',
                  label: 'Raw OCSF',
                  content: (
                    <div className="soc-code-block" style={{ margin: '8px 10px' }}>
                      <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                        {raw ? JSON.stringify(JSON.parse(raw), null, 2) : '(raw payload not available)'}
                      </pre>
                    </div>
                  ),
                },
              ]}
            />
          </Container></div>
        </div>
      </SpaceBetween>
    </ContentLayout>
  );
}
