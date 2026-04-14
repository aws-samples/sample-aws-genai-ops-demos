import { useState, useCallback, useRef, useEffect } from 'react'

interface Scenario {
  id: string
  name: string
  category: string
  severity: string
  devOpsAgentFeature: string
  featureDescription: string
  description: string
  apiPath?: string
  steps: string[]
  userImpact: string[]
  demoFlow: string[]
  available: boolean
}

interface ScenarioCardProps {
  scenario: Scenario
  injected: boolean
  expanded: boolean
  onToggleExpand: () => void
  devOpsAgentUrl: string
  triggerLambdaUrl: string | null
  statusLabels?: { pods: string; deployment: string; alarm: string }
  remainingSeconds?: number
  // Status panel data (only shown for expanded card)
  pods?: Array<{ name: string; status: string; ready: boolean; restarts: number }>
  deployment?: { name: string; replicas: number; readyReplicas: number; availableReplicas: number } | null
  alarm?: { name?: string; state: string; reason?: string }
  eksPodsUrl?: string | null
  eksClusterUrl?: string | null
  alarmUrl?: string | null
  loading?: boolean
  onRefresh?: () => void
}

const AUTO_REVERT_SECONDS = 10 * 60

export default function ScenarioCard({
  scenario, injected, expanded, onToggleExpand, devOpsAgentUrl, triggerLambdaUrl, statusLabels, remainingSeconds: serverRemainingSeconds,
  pods, deployment, alarm, eksPodsUrl, eksClusterUrl, alarmUrl, loading, onRefresh,
}: ScenarioCardProps) {
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [lastAction, setLastAction] = useState<{ type: string; message: string; success: boolean } | null>(null)
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const autoRevertRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const startTimer = useCallback(() => {
    setRemainingSeconds(AUTO_REVERT_SECONDS)
    timerRef.current = setInterval(() => {
      setRemainingSeconds(prev => (prev === null || prev <= 1) ? 0 : prev - 1)
    }, 1000)
    autoRevertRef.current = setTimeout(async () => {
      try {
        await fetch(scenario.apiPath!, { method: 'DELETE' })
        setLastAction({ type: 'rollback', message: 'Auto-reverted after 10 minutes.', success: true })
        onRefresh?.()
      } catch { /* ignore */ }
      stopTimer()
    }, AUTO_REVERT_SECONDS * 1000)
  }, [scenario.apiPath, onRefresh])

  const stopTimer = useCallback(() => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
    if (autoRevertRef.current) { clearTimeout(autoRevertRef.current); autoRevertRef.current = null }
    setRemainingSeconds(null)
  }, [])

  useEffect(() => () => { stopTimer() }, [stopTimer])

  async function handleInject() {
    setActionLoading('inject')
    setLastAction(null)
    try {
      const res = await fetch(scenario.apiPath!, { method: 'POST' })
      const data = await res.json()
      setLastAction({ type: 'inject', message: data.message, success: data.success })
      if (data.success) startTimer()
      setTimeout(() => onRefresh?.(), 2000)
    } catch (e: any) {
      setLastAction({ type: 'inject', message: e.message, success: false })
    } finally {
      setActionLoading(null)
    }
  }

  async function handleRollback() {
    setActionLoading('rollback')
    setLastAction(null)
    try {
      const res = await fetch(scenario.apiPath!, { method: 'DELETE' })
      const data = await res.json()
      setLastAction({ type: 'rollback', message: data.message, success: data.success })
      if (data.success) stopTimer()
      setTimeout(() => onRefresh?.(), 2000)
    } catch (e: any) {
      setLastAction({ type: 'rollback', message: e.message, success: false })
    } finally {
      setActionLoading(null)
    }
  }

  return (
    <div
      className={`scenario-card ${!scenario.available ? 'scenario-coming-soon' : ''} ${expanded ? 'scenario-expanded' : ''}`}
      onClick={() => scenario.available && onToggleExpand()}
    >
      {/* Card Header */}
      <div className="scenario-card-header">
        <div className="scenario-info">
          <div className="scenario-badge-row">
            {scenario.available && injected && <span className="badge badge-danger">ACTIVE</span>}
            {scenario.available && !injected && <span className="badge badge-healthy">HEALTHY</span>}
            {!scenario.available && <span className="badge badge-coming-soon">Coming Soon</span>}
          </div>
          <h2>{scenario.name}</h2>
          <div className="scenario-feature-tag">
            <span className="feature-label">AWS DevOps Agent feature showcased:</span> {scenario.devOpsAgentFeature}
          </div>
          <p className="scenario-description">{scenario.description}</p>
        </div>
        {scenario.available && (
          <div className="scenario-actions" onClick={e => e.stopPropagation()}>
            {(serverRemainingSeconds ?? remainingSeconds ?? 0) > 0 && (
              <div className="countdown-timer">
                <span className="countdown-label">Auto-revert in</span>
                <span className="countdown-value">
                  {Math.floor((serverRemainingSeconds ?? remainingSeconds ?? 0) / 60)}:{((serverRemainingSeconds ?? remainingSeconds ?? 0) % 60).toString().padStart(2, '0')}
                </span>
              </div>
            )}
            <button className="btn btn-danger" onClick={handleInject} disabled={actionLoading !== null || injected}>
              {actionLoading === 'inject' ? 'Injecting...' : '⚡ Inject'}
            </button>
            <button className="btn btn-success" onClick={handleRollback} disabled={actionLoading !== null || !injected}>
              {actionLoading === 'rollback' ? 'Rolling back...' : '↩ Rollback'}
            </button>
          </div>
        )}
      </div>

      {/* Action feedback */}
      {lastAction && (
        <div className={`alert ${lastAction.success ? 'alert-success' : 'alert-error'}`} style={{ marginTop: '0.75rem' }}>
          <span className="alert-icon">{lastAction.success ? '✓' : '✗'}</span>
          <div><strong>{lastAction.type === 'inject' ? 'Inject' : 'Rollback'}</strong><p>{lastAction.message}</p></div>
        </div>
      )}

      {/* Expanded Content */}
      {expanded && scenario.available && (
        <div className="scenario-expanded-content">
          <div className="scenario-info-grid">
            <div className="info-section">
              <h3><span className="info-icon">💥</span> Customer impact</h3>
              <ul className="info-list">
                {scenario.userImpact.map((item, i) => <li key={i}>{item}</li>)}
              </ul>
            </div>

            <div className="info-section">
              <h3><span className="info-icon">⛓</span> Incident chain</h3>
              <ol className="info-list info-list-ordered">
                {scenario.steps.map((step, i) => <li key={i}>{step}</li>)}
              </ol>
              {triggerLambdaUrl && (
                <a href={triggerLambdaUrl} target="_blank" rel="noopener noreferrer" className="console-link-inline">
                  View webhook trigger Lambda ↗
                </a>
              )}
            </div>

            <div className="info-section info-section-highlight">
              <h3><span className="info-icon">🎯</span> Demo walkthrough</h3>
              <ol className="info-list info-list-ordered">
                {scenario.demoFlow.map((step, i) => <li key={i}>{step}</li>)}
              </ol>
              <a href={devOpsAgentUrl} target="_blank" rel="noopener noreferrer" className="devops-agent-link">
                Open DevOps Agent console (Operator Access) ↗
              </a>
            </div>
          </div>

          {/* Live Status */}
          <div className="status-grid">
            <div className="status-card">
              <h3>
                <span className="status-icon">⊞</span> {statusLabels?.pods ?? 'Pods'}
                {eksPodsUrl && <a href={eksPodsUrl} target="_blank" rel="noopener noreferrer" className="console-link">Console ↗</a>}
              </h3>
              {loading ? <p className="status-loading">Loading...</p> : pods && pods.length > 0 ? (
                <div className="pod-list">
                  {pods.map(pod => {
                    const isHealthy = pod.ready && pod.status === 'Running'
                    const isPending = pod.status === 'Pending' || pod.status === 'ContainerCreating'
                    const colorClass = isHealthy ? 'pod-healthy' : isPending ? 'pod-pending' : 'pod-unhealthy'
                    const dotClass = isHealthy ? 'dot-green' : isPending ? 'dot-yellow' : 'dot-red'
                    return (
                    <div key={pod.name} className={`pod-item ${colorClass}`}>
                      <span className={`pod-dot ${dotClass}`} />
                      <div className="pod-info">
                        <span className="pod-name">{pod.name}</span>
                        <span className="pod-status">{pod.status}</span>
                      </div>
                      {pod.restarts > 0 && <span className="pod-restarts">{pod.restarts} restarts</span>}
                    </div>
                    )
                  })}
                </div>
              ) : <p className={injected ? "status-empty-danger" : "status-empty"}>No pods found</p>}
            </div>

            <div className="status-card">
              <h3>
                <span className="status-icon">◈</span> {statusLabels?.deployment ?? 'Deployment'}
                {eksClusterUrl && <a href={eksClusterUrl} target="_blank" rel="noopener noreferrer" className="console-link">Console ↗</a>}
              </h3>
              {loading ? <p className="status-loading">Loading...</p> : deployment ? (
                <div className="deployment-info">
                  <div className="deployment-row"><span className="deployment-label">Name</span><span className="deployment-value">{deployment.name}</span></div>
                  <div className="deployment-row">
                    <span className="deployment-label">Replicas</span>
                    <span className={`deployment-value ${(deployment.readyReplicas || 0) === 0 ? 'text-danger' : (deployment.readyReplicas || 0) < deployment.replicas ? 'text-warning' : 'text-healthy'}`}>
                      {deployment.readyReplicas || 0}/{deployment.replicas} ready
                    </span>
                  </div>
                  <div className="deployment-row">
                    <span className="deployment-label">Available</span>
                    <span className={`deployment-value ${(deployment.availableReplicas || 0) === 0 ? 'text-danger' : 'text-healthy'}`}>
                      {deployment.availableReplicas || 0}
                    </span>
                  </div>
                </div>
              ) : <p className="status-empty">Deployment not found</p>}
            </div>

            <div className="status-card">
              <h3>
                <span className="status-icon">🔔</span> {statusLabels?.alarm ?? 'Alarm'}
                {alarmUrl && <a href={alarmUrl} target="_blank" rel="noopener noreferrer" className="console-link">Console ↗</a>}
              </h3>
              {loading ? <p className="status-loading">Loading...</p> : alarm ? (
                <div className="alarm-info">
                  <div className={`alarm-state ${alarm.state === 'ALARM' ? 'alarm-firing' : alarm.state === 'OK' ? 'alarm-ok' : 'alarm-unknown'}`}>
                    {alarm.state}
                  </div>
                  {alarm.name && <span className="alarm-name">{alarm.name}</span>}
                </div>
              ) : <p className="status-empty">Not configured</p>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
