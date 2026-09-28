import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Cards from '@cloudscape-design/components/cards'
import ColumnLayout from '@cloudscape-design/components/column-layout'
import ExpandableSection from '@cloudscape-design/components/expandable-section'
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs'
import Link from '@cloudscape-design/components/link'
import ProgressBar from '@cloudscape-design/components/progress-bar'
import SpaceBetween from '@cloudscape-design/components/space-between'
import StatusIndicator, { StatusIndicatorProps } from '@cloudscape-design/components/status-indicator'
import Steps from '@cloudscape-design/components/steps'
import TextContent from '@cloudscape-design/components/text-content'
import { Alarm, Pod, Run, Scenario, ScenarioStatus, consoleLinks, formatCountdown } from './api'

export type LabState = 'healthy' | 'injecting' | 'injected' | 'reverting' | 'manual' | 'unknown'

/** What the presenter needs to know, derived from the cluster probe and the engine run. */
export function deriveState(st?: ScenarioStatus): LabState {
  if (!st) return 'unknown'
  const run = st.run
  if (run && run.status === 'RUNNING') {
    const phase = (id: string) => run.phases.find(p => p.id === id)?.status
    if (phase('inject') !== 'success') return 'injecting'
    if (phase('await-rollback') === 'success' || phase('revert') === 'in-progress') return 'reverting'
    return 'injected'
  }
  if (st.injected) return 'manual'
  return 'healthy'
}

// The value of the "Injection" key: what the Lab is doing to the cluster for this
// scenario. Never "healthy": health is a property of the cluster, shown in Live status.
const STATE_INDICATOR: Record<LabState, { type: StatusIndicatorProps.Type; label: string }> = {
  healthy: { type: 'stopped', label: 'Not injected' },
  injecting: { type: 'in-progress', label: 'Injecting' },
  injected: { type: 'error', label: 'Injected' },
  reverting: { type: 'in-progress', label: 'Reverting' },
  manual: { type: 'warning', label: 'Injected outside the Lab' },
  unknown: { type: 'loading', label: 'Checking' },
}

function podIndicator(pod: Pod) {
  const healthy = pod.ready && pod.status === 'Running'
  const pending = pod.status === 'Pending' || pod.status === 'ContainerCreating'
  const type: StatusIndicatorProps.Type = healthy ? 'success' : pending ? 'pending' : 'error'
  return (
    <StatusIndicator key={pod.name} type={type}>
      {pod.status}{pod.restarts > 0 ? ` (${pod.restarts} restarts)` : ''}
      <Box variant="span" color="text-body-secondary" fontSize="body-s"> {pod.name}</Box>
    </StatusIndicator>
  )
}

function alarmIndicator(alarm?: Alarm | null) {
  if (!alarm) return <StatusIndicator type="stopped">Not configured</StatusIndicator>
  const map: Record<string, StatusIndicatorProps.Type> = {
    ALARM: 'error', OK: 'success', INSUFFICIENT_DATA: 'pending', NOT_FOUND: 'stopped', ERROR: 'warning',
  }
  return <StatusIndicator type={map[alarm.state] ?? 'info'}>{alarm.state.replace('_', ' ')}</StatusIndicator>
}

/** Engine run as Cloudscape Steps: one per durable step, statuses from the execution history. */
export function RunSteps({ run, remaining }: { run: Run; remaining: number | null }) {
  return (
    <Steps
      steps={run.phases.map(p => ({
        status: p.status,
        header: p.label,
        statusIconAriaLabel: p.status,
        details: p.id === 'await-rollback' && p.status === 'in-progress' && remaining !== null
          ? `Auto-revert in ${formatCountdown(remaining)}`
          : p.id === 'await-rollback' && p.status === 'success'
            ? run.revertReason === 'auto' ? 'Timed out: reverted automatically' : 'Rollback requested'
            : undefined,
      }))}
    />
  )
}

export interface ScenarioCardsProps {
  scenarios: Scenario[]
  statuses: Record<string, ScenarioStatus>
  links: ReturnType<typeof consoleLinks>
  /** scenario id currently owned by an engine run, if any */
  busy: string | null
  /** id of the scenario an inject/rollback call is in flight for */
  acting: string | null
  /** remaining seconds before auto-revert, per scenario, ticking locally */
  remaining: Record<string, number | null>
  loading: boolean
  onInject: (id: string) => void
  onRollback: (id: string) => void
}

export default function ScenarioCards({ scenarios, statuses, links, busy, acting, remaining, loading, onInject, onRollback }: ScenarioCardsProps) {
  return (
    <Cards
      items={scenarios}
      trackBy="id"
      // Each card carries a 3-4 column live-status block: one per row unless the screen is wide.
      cardsPerRow={[{ cards: 1 }, { minWidth: 1400, cards: 2 }]}
      loading={loading && scenarios.length === 0}
      loadingText="Loading scenarios"
      empty={<Box textAlign="center" color="inherit">No scenarios in lab/scenarios.yaml</Box>}
      cardDefinition={{
        header: item => {
          const st = statuses[item.id]
          const state = deriveState(st)
          const ind = STATE_INDICATOR[state]
          const canInject = state === 'healthy' && !busy && acting === null
          const canRollback = (state === 'injected' || state === 'manual' || state === 'injecting') && acting === null
          return (
            <SpaceBetween size="s">
              <Box variant="h2" fontSize="heading-m">
                <Box variant="span" color="text-body-secondary" fontSize="heading-s" fontWeight="normal">Scenario: </Box>
                {item.name}
              </Box>
              <KeyValuePairs
                columns={3}
                items={[
                  { label: 'Failure injection', value: <StatusIndicator type={ind.type}>{ind.label}</StatusIndicator> },
                  { label: 'Category', value: item.category },
                  { label: 'Agent trigger', value: item.triggersAlarm ? 'CloudWatch alarm (automatic investigation)' : 'Chat prompt (manual)' },
                ]}
              />
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="primary" iconName="flag" loading={acting === item.id && state === 'healthy'}
                        disabled={!canInject} onClick={() => onInject(item.id)}>
                  Inject
                </Button>
                <Button iconName="undo" loading={acting === item.id && state !== 'healthy'}
                        disabled={!canRollback} onClick={() => onRollback(item.id)}>
                  Rollback
                </Button>
              </SpaceBetween>
            </SpaceBetween>
          )
        },
        sections: [
          {
            id: 'description',
            content: item => (
              <SpaceBetween size="xs">
                <Box variant="p">{item.description}</Box>
                {item.demonstrates?.check && (
                  <KeyValuePairs
                    columns={2}
                    items={[
                      { label: 'DevOps Agent capability shown', value: item.demonstrates.check },
                      ...(item.demonstrates.withCapability ? [{ label: 'What the agent should conclude', value: item.demonstrates.withCapability }] : []),
                    ]}
                  />
                )}
              </SpaceBetween>
            ),
          },
          {
            id: 'status',
            header: 'Live cluster status',
            content: item => {
              const st = statuses[item.id]
              const dep = st?.deployment
              const total = item.autoRevertSeconds || 600
              const left = remaining[item.id]
              const pairs = [
                {
                  label: 'Deployment',
                  value: dep ? (
                    <ProgressBar
                      variant="key-value"
                      value={dep.replicas > 0 ? Math.round((dep.readyReplicas / dep.replicas) * 100) : 0}
                      status={dep.replicas > 0 && dep.readyReplicas === 0 ? 'error' : 'in-progress'}
                      label={dep.name}
                      description={`${dep.namespace}`}
                      additionalInfo={`${dep.readyReplicas}/${dep.replicas} replicas ready`}
                    />
                  ) : <StatusIndicator type="stopped">{st?.error ? 'Unreachable' : 'Not found'}</StatusIndicator>,
                  info: links.eksCluster ? <Link external href={links.eksCluster} variant="info">Console</Link> : undefined,
                },
                {
                  label: 'Pods',
                  value: st?.pods && st.pods.length > 0
                    ? <SpaceBetween size="xxs">{st.pods.map(podIndicator)}</SpaceBetween>
                    : <StatusIndicator type={st?.injected ? 'error' : 'stopped'}>No pods</StatusIndicator>,
                  info: dep && links.eksPods(dep.namespace) ? <Link external href={links.eksPods(dep.namespace)!} variant="info">Console</Link> : undefined,
                },
                {
                  label: 'Alarm',
                  value: (
                    <SpaceBetween size="xxs">
                      {alarmIndicator(st?.alarm)}
                      {st?.alarm?.name && <Box fontSize="body-s" color="text-body-secondary">{st.alarm.name}</Box>}
                    </SpaceBetween>
                  ),
                  info: links.alarm(st?.alarm?.name) ? <Link external href={links.alarm(st?.alarm?.name)!} variant="info">Console</Link> : undefined,
                },
              ]
              if (left !== null && left !== undefined) {
                pairs.push({
                  label: 'Auto-revert',
                  value: (
                    <ProgressBar
                      variant="key-value"
                      value={Math.round(((total - left) / total) * 100)}
                      label="Time until the engine reverts"
                      additionalInfo={formatCountdown(left)}
                      description={`Rolls back on its own after ${Math.round(total / 60)} minutes`}
                    />
                  ),
                  info: undefined,
                })
              }
              return (
                <SpaceBetween size="s">
                  {st?.error && <StatusIndicator type="warning">Cluster probe failed: {st.error}</StatusIndicator>}
                  <KeyValuePairs columns={pairs.length} items={pairs} />
                </SpaceBetween>
              )
            },
          },
          {
            id: 'run',
            content: item => {
              const st = statuses[item.id]
              const run = st?.run ?? st?.lastRun
              if (!run) return null
              const active = run.status === 'RUNNING'
              const ended = run.endedAt ? new Date(run.endedAt * 1000).toLocaleTimeString() : ''
              return (
                <ExpandableSection
                  variant="footer"
                  defaultExpanded={active}
                  headerText={active ? 'Engine run in progress' : `Last engine run: ${run.status.toLowerCase()}${ended ? ` at ${ended}` : ''}`}
                >
                  <RunSteps run={run} remaining={remaining[item.id] ?? null} />
                </ExpandableSection>
              )
            },
          },
          {
            id: 'walkthrough',
            content: item => (
              <ExpandableSection variant="footer" headerText="Walkthrough">
                <SpaceBetween size="m">
                  <ColumnLayout columns={3} variant="text-grid">
                    <div>
                      <Box variant="h4">Customer impact</Box>
                      <TextContent><ul>{(item.customerImpact ?? []).map((t, i) => <li key={i}>{t}</li>)}</ul></TextContent>
                    </div>
                    <div>
                      <Box variant="h4">Incident chain</Box>
                      <TextContent><ol>{(item.incidentChain ?? []).map((t, i) => <li key={i}>{t}</li>)}</ol></TextContent>
                      {links.triggerLambda && <Link external href={links.triggerLambda}>Webhook trigger Lambda</Link>}
                    </div>
                    <div>
                      <Box variant="h4">Demo flow</Box>
                      <TextContent><ol>{(item.demoFlow ?? []).map((t, i) => <li key={i}>{t}</li>)}</ol></TextContent>
                      {links.devOpsAgent && <Link external href={links.devOpsAgent}>DevOps Agent console</Link>}
                    </div>
                  </ColumnLayout>
                  {item.talkTrack && (
                    <Box variant="p" color="text-body-secondary"><i>“{item.talkTrack}”</i></Box>
                  )}
                </SpaceBetween>
              </ExpandableSection>
            ),
          },
        ],
      }}
    />
  )
}
