import { useState, useEffect, useCallback, useRef } from 'react'
import Markdown from 'react-markdown'
import ScenarioCard from '../components/ScenarioCard'
import './DevOpsAgentLabPage.css'

interface SystemStatus {
  success: boolean
  pods: Array<{ name: string; status: string; ready: boolean; restarts: number }>
  deployment: { name: string; replicas: number; readyReplicas: number; availableReplicas: number } | null
  alarm: { name?: string; state: string; reason?: string }
  injected: boolean | null
  scenarios?: Record<string, {
    injected: boolean
    remainingSeconds?: number
    pods?: Array<{ name: string; status: string; ready: boolean; restarts: number }>
    alarm?: { name?: string; state: string; reason?: string }
  }>
  k8sError?: string
  region?: string
  clusterName?: string
  namespace?: string
  triggerLambdaName?: string
  devOpsAgentRegion?: string
  devOpsAgentSpaceId?: string
}

const SCENARIOS = [
  {
    id: 'db-connection-failure',
    name: 'Database Connection Failure',
    category: 'Database',
    severity: 'Critical',
    devOpsAgentFeature: 'Automated Incident Investigation',
    featureDescription: '',
    description: 'Sets a wrong database password on the payment-processor deployment, causing CrashLoopBackOff. Fluent Bit ships error logs to CloudWatch, where a metric filter triggers an alarm. The alarm notifies an SNS topic, which invokes a Lambda function that sends an HMAC-signed webhook to the DevOps Agent — kicking off an autonomous investigation. The agent checks pods, logs, RDS connectivity, and security groups, then delivers a root cause analysis with remediation steps.',
    apiPath: '/admin/scenarios/db-connection-failure/inject',
    statusLabels: { pods: 'Pods', deployment: 'Payment Processor', alarm: 'DB Connection Alarm' },
    steps: [
      'Payment Processor pod gets wrong DB_PASSWORD',
      'Pod crashes → CrashLoopBackOff',
      'Fluent Bit ships error logs → CloudWatch',
      'Metric filter detects "database connection" errors',
      'CloudWatch Alarm triggers → SNS → Lambda',
      'Lambda sends HMAC webhook → DevOps Agent investigates',
    ],
    userImpact: [
      'Checkout fails with "Payment processor is unavailable" error message',
      'Transaction history page may show empty results or fail to load',
      'Product catalog and cart continue to work (merchant-gateway is unaffected)',
      'The error is visible immediately when attempting any payment operation',
    ],
    demoFlow: [
      'Click "Inject" above and wait ~10 seconds for the pod to crash',
      'Go to the Shop tab → add an item to cart → try to checkout — you\'ll see the payment fail',
      'Come back here and watch the alarm flip to ALARM (~2 min)',
      'Open the DevOps Agent console (link below) — a new investigation will appear automatically',
      'Watch the agent check pods, logs, RDS connectivity, and security groups',
      'The agent delivers a root cause analysis with remediation steps',
      'Click "Rollback" to restore the system when done',
    ],
    available: true,
  },
  {
    id: 'dns-resolution-failure',
    name: 'DNS Resolution Failure',
    category: 'Network',
    severity: 'Critical',
    devOpsAgentFeature: 'Automated Incident Investigation',
    featureDescription: '',
    description: 'Scales CoreDNS to 0 replicas, breaking all service-to-service DNS resolution. Pods stay Running and Ready (health checks use IP), but every inter-service call fails with ENOTFOUND. The agent must trace across namespaces to find the root cause in kube-system — the classic "it\'s always DNS" scenario.',
    apiPath: '/admin/scenarios/dns-resolution-failure/inject',
    statusLabels: { pods: 'CoreDNS Pods', deployment: 'CoreDNS Deployment', alarm: 'DNS Resolution Alarm' },
    steps: [
      'CoreDNS deployment scaled to 0 replicas in kube-system',
      'All DNS queries in the cluster start failing',
      'merchant-gateway cannot resolve payment-processor hostname',
      'API calls fail with ENOTFOUND — but all pods show Running/Ready',
      'Fluent Bit ships error logs → CloudWatch',
      'DNS resolution error alarm triggers → SNS → Lambda → DevOps Agent',
    ],
    userImpact: [
      'All pages hang then fail — every API call depends on DNS',
      'Checkout, transaction history, and catalog all return errors',
      'Confusing: pod status shows healthy everywhere, no obvious crash',
      'The classic "it\'s always DNS" — hardest to diagnose manually',
    ],
    demoFlow: [
      'Click "Inject" — CoreDNS will be scaled to 0',
      'Try any action in the app — everything will fail or hang',
      'Come back here — pods still show Running (that\'s the trap)',
      'Wait ~2 min for the alarm to fire',
      'Open the DevOps Agent console — watch it trace from app errors to CoreDNS',
      'The agent identifies CoreDNS in kube-system as the root cause',
      'Click "Rollback" to restore DNS resolution',
    ],
    available: true,
  },
]

const SKILL_NAME = 'helios-incident-report'

const SKILL_DESCRIPTION = 'Incident reporting standard for the Helios Commerce platform operated by ACME Tech. Use when investigating any incident affecting the Helios payment processing system including CloudWatch alarms, EKS pod failures, database issues, or DNS resolution problems. Enforces business context, SLA tracking, and executive-ready report format.'

const SKILL_INSTRUCTIONS = `# Helios Commerce — Incident Report Standard

## Business Context

Helios Commerce is ACME Tech's flagship e-commerce payment platform.
- **Revenue impact**: Helios processes ~\\€2.4M in daily transactions across 3 merchant accounts
- **SLA commitment**: 99.95% uptime (max 21.6 min/month downtime) per merchant contract
- **Peak hours**: 10:00–14:00 and 18:00–22:00 CET — incidents during peak have 3x revenue impact
- **Compliance**: PCI-DSS Level 1 — any credential exposure must be reported to the security team within 1 hour
- **Escalation**: CRITICAL incidents must notify the VP of Engineering (on-call rotation in PagerDuty)

## Report Format

Structure every investigation report with these sections:

### 1. Executive Summary
One paragraph: what happened, when it started, current status, and business severity.
Always state whether this occurred during peak hours.

### 2. Business Impact
- Estimated failed transactions and revenue loss (use \\€2.4M/day / 1440 min = ~\\€1,667/min)
- Which merchant accounts are affected (Helios Electronics, TechStore Global, Fashion Boutique)
- SLA budget consumed: calculate minutes of downtime vs. 21.6 min monthly budget

### 3. Root Cause
- Technical root cause with evidence (log lines, metrics, config diffs)
- How it was introduced and by whom (deployment, manual change, drift)

### 4. Severity Classification
Apply these rules:
- **P1 CRITICAL**: All payment processing down, or credential exposure (PCI-DSS breach)
- **P2 HIGH**: Single merchant affected, or degraded performance >50% error rate
- **P3 MEDIUM**: Intermittent errors, <10% of transactions affected
- **SECURITY**: Any plain-text credentials in env vars or logs — flag for immediate PCI review

### 5. Remediation & Next Steps
- Immediate fix (exact commands)
- Post-incident: what monitoring would catch this faster
- Architecture improvement to prevent recurrence`

const ADMIN_API_BASE = '/admin'

export default function DevOpsAgentLabPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [expandedScenario, setExpandedScenario] = useState<string | null>(null)
  const [skillExpanded, setSkillExpanded] = useState(false)
  const [mcpExpanded, setMcpExpanded] = useState(false)
  const [copiedField, setCopiedField] = useState<string | null>(null)
  const [usage, setUsage] = useState<any>(null)

  const copyToClipboard = (text: string, field: string) => {
    navigator.clipboard.writeText(text)
    setCopiedField(field)
    setTimeout(() => setCopiedField(null), 2000)
  }
  const [logs, setLogs] = useState<any[]>([])
  const [activeSection, setActiveSection] = useState('scenarios')

  const scenariosRef = useRef<HTMLDivElement>(null)
  const skillsRef = useRef<HTMLDivElement>(null)
  const mcpRef = useRef<HTMLDivElement>(null)
  const logsRef = useRef<HTMLDivElement>(null)
  const usageRef = useRef<HTMLDivElement>(null)

  const scrollToSection = (id: string) => {
    const refs: Record<string, React.RefObject<HTMLDivElement | null>> = { scenarios: scenariosRef, skills: skillsRef, mcp: mcpRef, logs: logsRef, usage: usageRef }
    refs[id]?.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.id)
          }
        }
      },
      { rootMargin: '-80px 0px -60% 0px', threshold: 0 }
    )
    for (const ref of [scenariosRef, skillsRef, mcpRef, logsRef, usageRef]) {
      if (ref.current) observer.observe(ref.current)
    }
    return () => observer.disconnect()
  }, [usage, logs])

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${ADMIN_API_BASE}/status`)
      if (!res.ok) throw new Error(`Status check failed: ${res.status}`)
      const data = await res.json()
      setStatus(data)
      setError(null)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchUsage = useCallback(async () => {
    try {
      const res = await fetch(`${ADMIN_API_BASE}/usage`)
      if (res.ok) {
        const data = await res.json()
        if (data.success) setUsage(data)
      }
    } catch { /* ignore — usage is optional */ }
  }, [])

  const fetchLogs = useCallback(async () => {
    try {
      const res = await fetch(`${ADMIN_API_BASE}/logs`)
      if (res.ok) {
        const data = await res.json()
        if (data.success) setLogs(data.logs || [])
      }
    } catch { /* ignore — logs are optional */ }
  }, [])

  useEffect(() => {
    fetchStatus()
    fetchUsage()
    fetchLogs()
    const interval = setInterval(fetchStatus, 10000)
    return () => clearInterval(interval)
  }, [fetchStatus, fetchUsage, fetchLogs])

  const region = status?.region || 'us-east-1'
  const clusterName = status?.clusterName || ''
  const namespace = status?.namespace || 'payment-demo'

  const eksPodsUrl = clusterName
    ? `https://${region}.console.aws.amazon.com/eks/home?region=${region}#/clusters/${clusterName}/pods?namespace=${namespace}`
    : null
  const eksClusterUrl = clusterName
    ? `https://${region}.console.aws.amazon.com/eks/home?region=${region}#/clusters/${clusterName}`
    : null
  const alarmUrl = status?.alarm?.name
    ? `https://${region}.console.aws.amazon.com/cloudwatch/home?region=${region}#alarmsV2:alarm/${encodeURIComponent(status.alarm.name)}`
    : null
  const devOpsAgentRegion = status?.devOpsAgentRegion || 'us-east-1'
  const devOpsAgentSpaceId = status?.devOpsAgentSpaceId || ''
  const devOpsAgentUrl = devOpsAgentSpaceId
    ? `https://${devOpsAgentSpaceId}.aidevops.global.app.aws/`
    : `https://${devOpsAgentRegion}.console.aws.amazon.com/aidevops/home#/agent-spaces`
  const skillsUrl = devOpsAgentSpaceId
    ? `https://${devOpsAgentSpaceId}.aidevops.global.app.aws/skills`
    : `https://${devOpsAgentRegion}.console.aws.amazon.com/aidevops/home#/agent-spaces`
  const triggerLambdaName = status?.triggerLambdaName || ''
  const triggerLambdaUrl = triggerLambdaName
    ? `https://${region}.console.aws.amazon.com/lambda/home?region=${region}#/functions/${triggerLambdaName}`
    : null

  const filteredScenarios = SCENARIOS.filter(s =>
    s.name.toLowerCase().includes(search.toLowerCase()) ||
    s.category.toLowerCase().includes(search.toLowerCase()) ||
    s.description.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="lab-page">
      <div className="lab-header">
        <div>
          <h1>⚡ DevOps Agent Lab</h1>
          <p className="lab-subtitle">
            Your demo control center for AWS DevOps Agent
          </p>
        </div>
        <button className="btn-refresh" onClick={() => { fetchStatus(); fetchUsage(); fetchLogs() }} disabled={loading}>↻ Refresh</button>
      </div>

      {/* Sticky section nav */}
      <nav className="lab-nav">
        {[
          { id: 'scenarios', icon: '🔥', label: 'Scenarios' },
          { id: 'skills', icon: '🧠', label: 'Skills' },
          { id: 'mcp', icon: '🔌', label: 'MCP Tools' },
          ...(logs.length > 0 ? [{ id: 'logs', icon: '📋', label: 'Logs' }] : []),
          ...(usage ? [{ id: 'usage', icon: '📊', label: 'Usage' }] : []),
        ].map(({ id, icon, label }) => (
          <button
            key={id}
            className={`lab-nav-item ${activeSection === id ? 'lab-nav-active' : ''}`}
            onClick={() => scrollToSection(id)}
          >
            <span className="lab-nav-icon">{icon}</span> {label}
          </button>
        ))}
      </nav>

      {error && (
        <div className="alert alert-error">
          <span className="alert-icon">⚠</span>
          <div><strong>Connection Error</strong><p>{error}</p></div>
        </div>
      )}

      {/* ── Section 1: Failure Scenarios ── */}
      <div className="lab-section" id="scenarios" ref={scenariosRef}>
        <div className="lab-section-header">
          <h2>🔥 Failure Scenarios</h2>
          <p className="lab-section-description">Inject real infrastructure failures to trigger automated DevOps Agent investigations</p>
        </div>

        <div className="search-bar">
          <span className="search-icon">🔍</span>
          <input
            type="text"
            placeholder="Search scenarios... (e.g. database, dns, network)"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="search-input"
          />
          {search && <button className="search-clear" onClick={() => setSearch('')}>✕</button>}
        </div>

        <div className="scenario-gallery">
          {filteredScenarios.map(scenario => (
            <ScenarioCard
              key={scenario.id}
              scenario={scenario}
              injected={status?.scenarios?.[scenario.id]?.injected ?? false}
              expanded={expandedScenario === scenario.id}
              onToggleExpand={() => setExpandedScenario(expandedScenario === scenario.id ? null : scenario.id)}
              devOpsAgentUrl={devOpsAgentUrl}
              triggerLambdaUrl={triggerLambdaUrl}
              statusLabels={scenario.statusLabels}
              remainingSeconds={status?.scenarios?.[scenario.id]?.remainingSeconds}
              pods={status?.scenarios?.[scenario.id]?.pods ?? status?.pods}
              deployment={status?.deployment}
              alarm={status?.scenarios?.[scenario.id]?.alarm ?? status?.alarm}
              eksPodsUrl={eksPodsUrl}
              eksClusterUrl={eksClusterUrl}
              alarmUrl={
                status?.scenarios?.[scenario.id]?.alarm?.name
                  ? `https://${region}.console.aws.amazon.com/cloudwatch/home?region=${region}#alarmsV2:alarm/${encodeURIComponent(status.scenarios![scenario.id].alarm!.name!)}`
                  : alarmUrl
              }
              loading={loading}
              onRefresh={fetchStatus}
            />
          ))}
        </div>

        {filteredScenarios.length === 0 && (
          <div className="no-results">No scenarios match "{search}"</div>
        )}
      </div>

      {/* ── Section 2: Skills ── */}
      <div className="lab-section" id="skills" ref={skillsRef}>
        <div className="lab-section-header">
          <h2>🧠 Agent Skills</h2>
          <p className="lab-section-description">
            Skills encode your team's reporting standards and investigation criteria.
            They load automatically and influence how the agent reasons and reports.
          </p>
        </div>

        <div className="skill-card">
          <div className="skill-card-header" onClick={() => setSkillExpanded(!skillExpanded)}>
            <div className="skill-info">
              <span className="skill-name">{SKILL_NAME}</span>
              <span className="skill-description">
                Adds ACME Tech business context — revenue impact (€2.4M/day), SLA tracking (99.95%), PCI-DSS compliance rules, and severity classification. The agent already investigates; this skill teaches it your business standards.
              </span>
              <div className="scenario-feature-tag">
                <span className="feature-label">AWS DevOps Agent feature showcased:</span> Use a custom Skill during chat investigation to format report
              </div>
            </div>
            <span className="skill-toggle">{skillExpanded ? '▾' : '▸'}</span>
          </div>

          {skillExpanded && (
            <div className="skill-expanded">
              <div className="skill-instructions">
                <h3>Create this skill in Operator Access</h3>
                <p className="skill-instructions-subtitle">
                  Open the Skills page, click "Add skill" → "Create skill", then copy-paste each field below.
                </p>

                <div className="skill-fields">
                  <div className="skill-field">
                    <div className="skill-field-header">
                      <span className="skill-field-label">Name</span>
                      <button className="skill-copy-btn" onClick={() => copyToClipboard(SKILL_NAME, 'name')}>
                        {copiedField === 'name' ? '✓ Copied' : '📋 Copy'}
                      </button>
                    </div>
                    <code className="skill-field-value">{SKILL_NAME}</code>
                  </div>

                  <div className="skill-field">
                    <div className="skill-field-header">
                      <span className="skill-field-label">Description</span>
                      <button className="skill-copy-btn" onClick={() => copyToClipboard(SKILL_DESCRIPTION, 'description')}>
                        {copiedField === 'description' ? '✓ Copied' : '📋 Copy'}
                      </button>
                    </div>
                    <div className="skill-field-value skill-field-text">{SKILL_DESCRIPTION}</div>
                  </div>

                  <div className="skill-field-row">
                    <div className="skill-field">
                      <span className="skill-field-label">Status</span>
                      <code className="skill-field-value">Active</code>
                    </div>
                    <div className="skill-field">
                      <span className="skill-field-label">Agent Type</span>
                      <code className="skill-field-value">Generic</code>
                    </div>
                  </div>

                  <div className="skill-field">
                    <div className="skill-field-header">
                      <span className="skill-field-label">Instructions</span>
                      <button className="skill-copy-btn" onClick={() => copyToClipboard(SKILL_INSTRUCTIONS, 'instructions')}>
                        {copiedField === 'instructions' ? '✓ Copied' : '📋 Copy'}
                      </button>
                    </div>
                    <pre className="skill-field-code">{SKILL_INSTRUCTIONS}</pre>
                  </div>
                </div>

                <div className="skill-actions">
                  <a href={skillsUrl} target="_blank" rel="noopener noreferrer" className="btn btn-primary">
                    Open Skills in Operator Access ↗
                  </a>
                  <a href={devOpsAgentUrl} target="_blank" rel="noopener noreferrer" className="btn btn-secondary">
                    Open DevOps Agent Console (Operator Access) ↗
                  </a>
                </div>

                <div className="skill-next-step">
                  <h3>🎯 How to demo this skill</h3>
                  <p>
                    <strong>Automated investigations:</strong> The skill loads automatically when alarms trigger.
                    The agent uses the business context (revenue impact, SLA, severity rules) to enrich its reasoning.
                    Check the Logs section to see which skills were loaded per investigation.
                  </p>
                  <p style={{marginTop: '0.5rem'}}>
                    <strong>On-demand Chat:</strong> Open the DevOps Agent console, start a Chat, and describe a problem
                    (e.g. "We're seeing payment failures on Helios"). The agent follows the skill's report format
                    in its response — Executive Summary, Business Impact, Root Cause, Severity, Remediation.
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Section 3: MCP Tools ── */}
      <div className="lab-section" id="mcp" ref={mcpRef}>
        <div className="lab-section-header">
          <h2>🔌 MCP Tools</h2>
          <p className="lab-section-description">
            A custom MCP server gives the agent read-only access to the payment database via AgentCore Gateway.
            Logs tell you what broke — these tools tell you what it <em>cost</em>.
          </p>
        </div>

        <div className="skill-card">
          <div className="skill-card-header" onClick={() => setMcpExpanded(!mcpExpanded)}>
            <div className="skill-info">
              <span className="skill-name">pay-txn-mcp</span>
              <span className="skill-description">
                4 read-only tools that query the payment database — transaction volumes, failure details, processing gaps, and post-incident impact analysis. Connected via private connectivity (VPC Lattice + PrivateLink). No arbitrary SQL — fixed parameterized queries only.
              </span>
              <div className="scenario-feature-tag">
                <span className="feature-label">AWS DevOps Agent feature showcased:</span> Custom MCP Server via AgentCore Gateway
              </div>
            </div>
            <span className="skill-toggle">{mcpExpanded ? '▾' : '▸'}</span>
          </div>

          {mcpExpanded && (
            <div className="skill-expanded">
              <div className="skill-instructions">
                <h3>Available Tools</h3>
                <p className="skill-instructions-subtitle">
                  These tools are registered but not enabled by default. Enable them in the DevOps Agent Console to let the agent query the payment database during investigations.
                </p>

                <table className="mcp-tools-table">
                  <thead>
                    <tr><th>Tool</th><th>What It Returns</th><th>Why It Matters</th></tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td><code>get_transaction_summary</code></td>
                      <td>Transaction counts and totals grouped by status (last N minutes, max 60)</td>
                      <td>Health pulse — are payments flowing or stalled?</td>
                    </tr>
                    <tr>
                      <td><code>get_recent_failures</code></td>
                      <td>Failed transactions with error codes, merchant names, amounts (max 50 rows)</td>
                      <td>Which merchants are affected and why</td>
                    </tr>
                    <tr>
                      <td><code>get_processing_gap</code></td>
                      <td>Time since last captured payment + stuck authorization count</td>
                      <td>Detects the <em>absence</em> of activity — invisible in logs</td>
                    </tr>
                    <tr>
                      <td><code>get_incident_impact</code></td>
                      <td>Post-incident analysis: transactions by status, affected merchants, 24h baseline comparison</td>
                      <td>Full business impact report for any past time window</td>
                    </tr>
                  </tbody>
                </table>

                <h3>Enable in the DevOps Agent Console</h3>
                <ol className="mcp-setup-steps">
                  <li>Open the <strong>DevOps Agent Console</strong> → <strong>Capabilities</strong> (left nav)</li>
                  <li>Under <strong>MCP Servers</strong>, find <strong>pay-txn-mcp</strong></li>
                  <li>Click <strong>Edit</strong> and select the tools you want to enable</li>
                  <li>Save — the agent can now use these tools in Chat and during investigations</li>
                </ol>

                <div className="skill-actions">
                  <a href={devOpsAgentUrl} target="_blank" rel="noopener noreferrer" className="btn btn-primary">
                    Open DevOps Agent Console ↗
                  </a>
                </div>

                <h3>🎯 Prompts to try</h3>
                <div className="skill-field">
                  <div className="skill-field-header">
                    <span className="skill-field-label">During an active incident</span>
                    <button className="skill-copy-btn" onClick={() => copyToClipboard('What\'s the transaction volume over the last 60 minutes and how does the current processing gap compare to the pre-incident rate?', 'mcp-prompt-1')}>
                      {copiedField === 'mcp-prompt-1' ? '✓ Copied' : '📋 Copy'}
                    </button>
                  </div>
                  <code className="skill-field-value">What's the transaction volume over the last 60 minutes and how does the current processing gap compare to the pre-incident rate?</code>
                </div>

                <div className="skill-field">
                  <div className="skill-field-header">
                    <span className="skill-field-label">Post-incident analysis</span>
                    <button className="skill-copy-btn" onClick={() => copyToClipboard('Analyze the business impact of the incident between [START_TIME] and [END_TIME]. How many transactions were affected, which merchants were impacted, and how does it compare to the normal baseline?', 'mcp-prompt-2')}>
                      {copiedField === 'mcp-prompt-2' ? '✓ Copied' : '📋 Copy'}
                    </button>
                  </div>
                  <code className="skill-field-value">Analyze the business impact of the incident between [START_TIME] and [END_TIME]. How many transactions were affected, which merchants were impacted, and how does it compare to the normal baseline?</code>
                </div>

                <div className="skill-next-step">
                  <h3>💡 Combine with Skills</h3>
                  <p>
                    MCP tools provide the raw data — transaction counts, failure rates, processing gaps.
                    Skills provide the business context — SLA budgets, severity rules, report format.
                    When both are enabled, the agent can produce a complete executive-ready incident report
                    with real transaction data, revenue impact calculations, and compliance assessment.
                    For example, after an investigation completes, ask the agent in Chat to
                    "produce a post-incident report for the last outage" — it will combine MCP data with the
                    skill's reporting template.
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Section 4: Logs ── */}
      {logs.length > 0 && (
        <div className="lab-section" id="logs" ref={logsRef}>
          <div className="lab-section-header">
            <h2>📋 Logs</h2>
            <p className="lab-section-description">
              Recent DevOps Agent activity — investigations, evaluations, and system learning
            </p>
          </div>

          <div className="logs-list">
            {logs.map((log) => {
              const created = new Date(log.createdAt)
              const updated = new Date(log.updatedAt || log.createdAt)
              const durationSec = Math.round((updated.getTime() - created.getTime()) / 1000)
              const durationStr = durationSec >= 60
                ? `${Math.floor(durationSec / 60)}m ${durationSec % 60}s`
                : `${durationSec}s`

              return (
                <div key={log.taskId} className="log-card">
                  <div className="log-card-header">
                    <div className="log-badges">
                      <span className={`badge ${
                        log.status === 'COMPLETED' ? 'badge-healthy' :
                        log.status === 'IN_PROGRESS' ? 'badge-in-progress' :
                        log.status === 'FAILED' ? 'badge-danger' :
                        'badge-unknown'
                      }`}>{log.status}</span>
                      <span className={`badge badge-priority-${(log.priority || '').toLowerCase()}`}>{log.priority}</span>
                      <span className="badge badge-type">{log.taskType}</span>
                    </div>
                    <div className="log-header-right">
                      <span className="log-time">{created.toLocaleString()}</span>
                      {devOpsAgentSpaceId && (
                        <a
                          href={`https://${devOpsAgentSpaceId}.aidevops.global.app.aws/${devOpsAgentSpaceId}/investigation/${log.taskId}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="log-task-link"
                        >{log.taskId}</a>
                      )}
                    </div>
                  </div>

                  <h3 className="log-title">{log.title}</h3>

                  <div className="log-metrics">
                    <div className="log-metric">
                      <span className="log-metric-icon">⏱</span>
                      <span className="log-metric-value">{durationStr}</span>
                      <span className="log-metric-label">duration</span>
                    </div>
                    {log.toolCalls !== undefined && (
                      <div className="log-metric">
                        <span className="log-metric-icon">🔧</span>
                        <span className="log-metric-value">{log.toolCalls}</span>
                        <span className="log-metric-label">tool calls</span>
                      </div>
                    )}
                    {log.skillReads !== undefined && (
                      <div className="log-metric">
                        <span className="log-metric-icon">🧠</span>
                        <span className="log-metric-value">{log.skillReads}</span>
                        <span className="log-metric-label">
                          {log.skillNames && log.skillNames.length > 0
                            ? log.skillNames.join(', ')
                            : 'no skills loaded'}
                        </span>
                      </div>
                    )}
                    {log.journalRecordCount !== undefined && (
                      <div className="log-metric">
                        <span className="log-metric-icon">📝</span>
                        <span className="log-metric-value">{log.journalRecordCount}</span>
                        <span className="log-metric-label">journal records</span>
                      </div>
                    )}
                  </div>

                  {log.summaryMd && (
                    <div className="log-summary">
                      <Markdown>{log.summaryMd}</Markdown>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* ── Section 4: Account Usage ── */}
      {usage && (
        <div className="lab-section" id="usage" ref={usageRef}>
          <div className="lab-section-header">
            <h2>📊 Account Usage</h2>
            <p className="lab-section-description">
              Monthly DevOps Agent hours used vs. account quota
            </p>
          </div>

          <div className="usage-grid">
            {[
              { label: 'Investigation', data: usage.monthlyAccountInvestigationHours, icon: '🔍' },
              { label: 'Evaluation', data: usage.monthlyAccountEvaluationHours, icon: '📋' },
              { label: 'On-demand (Chat)', data: usage.monthlyAccountOnDemandHours, icon: '💬' },
              { label: 'System Learning', data: usage.monthlyAccountSystemLearningHours, icon: '🤖' },
            ].map(({ label, data, icon }) => (
              <div key={label} className="usage-card">
                <div className="usage-card-header">
                  <span className="usage-icon">{icon}</span>
                  <span className="usage-label">{label}</span>
                </div>
                <div className="usage-bar-container">
                  <div
                    className="usage-bar"
                    style={{ width: `${Math.min(100, (data.usage / data.limit) * 100)}%` }}
                  />
                </div>
                <div className="usage-values">
                  <span className="usage-current">{data.usage.toFixed(1)}h</span>
                  <span className="usage-limit">/ {data.limit}h</span>
                </div>
              </div>
            ))}
          </div>

          <div className="usage-cost-estimate">
            💰 Estimated spend: <strong>${((
              (usage.monthlyAccountInvestigationHours?.usage || 0) +
              (usage.monthlyAccountEvaluationHours?.usage || 0) +
              (usage.monthlyAccountOnDemandHours?.usage || 0) +
              (usage.monthlyAccountSystemLearningHours?.usage || 0)
            ) * 3600 * 0.0083).toFixed(2)}</strong> this month
            <span className="usage-cost-detail"> (total {((
              (usage.monthlyAccountInvestigationHours?.usage || 0) +
              (usage.monthlyAccountEvaluationHours?.usage || 0) +
              (usage.monthlyAccountOnDemandHours?.usage || 0) +
              (usage.monthlyAccountSystemLearningHours?.usage || 0)
            ) * 3600).toFixed(0)} agent-seconds × $0.0083/s)</span>
          </div>
        </div>
      )}
    </div>
  )
}
