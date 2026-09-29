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
import { Fact, Run, Scenario, ScenarioStatus, consoleLinks, formatCountdown } from './api'

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

// The value of the "Failure injection" key: what the Lab is doing to the environment for
// this scenario. Never "healthy": health is a property of the environment, shown as facts.
const STATE_INDICATOR: Record<LabState, { type: StatusIndicatorProps.Type; label: string }> = {
  healthy: { type: 'stopped', label: 'Not injected' },
  injecting: { type: 'in-progress', label: 'Injecting' },
  injected: { type: 'error', label: 'Injected' },
  reverting: { type: 'in-progress', label: 'Reverting' },
  manual: { type: 'warning', label: 'Injected outside the Lab' },
  unknown: { type: 'loading', label: 'Checking' },
}

/** Render one fact's value. The UI never interprets the domain: status, detail, items and
 *  progress come from the probe as-is. */
export function FactValue({ fact }: { fact: Fact }) {
  if (fact.progress) {
    const status = fact.status === 'error' ? 'error' : fact.status === 'success' ? 'success' : 'in-progress'
    return (
      <ProgressBar
        variant="key-value"
        value={fact.progress.percent}
        status={status}
        label={fact.value}
        description={fact.detail}
        additionalInfo={fact.progress.text}
      />
    )
  }
  if (fact.items) {
    return (
      <SpaceBetween size="xxs">
        {fact.items.map(it => (
          <StatusIndicator key={it.text} type={it.status ?? 'info'}>
            {it.text}
            {it.detail && <Box variant="span" color="text-body-secondary" fontSize="body-s"> {it.detail}</Box>}
          </StatusIndicator>
        ))}
        {fact.items.length === 0 && <StatusIndicator type={fact.status ?? 'stopped'}>{fact.value ?? 'None'}</StatusIndicator>}
      </SpaceBetween>
    )
  }
  return (
    <SpaceBetween size="xxs">
      {fact.status ? <StatusIndicator type={fact.status}>{fact.value ?? ''}</StatusIndicator> : <Box>{fact.value ?? '–'}</Box>}
      {fact.detail && <Box fontSize="body-s" color="text-body-secondary">{fact.detail}</Box>}
    </SpaceBetween>
  )
}

/** A fact as a KeyValuePairs item; the fact's link becomes the pair's info link. */
export function factToPair(fact: Fact) {
  return {
    label: fact.label,
    value: <FactValue fact={fact} />,
    info: fact.link ? <Link external href={fact.link.href} variant="info">{fact.link.text}</Link> : undefined,
  }
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
            header: 'Live status',
            content: item => {
              const st = statuses[item.id]
              const total = item.autoRevertSeconds || 600
              const left = remaining[item.id]
              const pairs = (st?.facts ?? []).map(factToPair)
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
                  {st?.error && <StatusIndicator type="warning">Probe failed: {st.error}</StatusIndicator>}
                  {pairs.length > 0
                    ? <KeyValuePairs columns={Math.min(pairs.length, 4)} items={pairs} />
                    : !st?.error && <StatusIndicator type="loading">Reading the environment</StatusIndicator>}
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
