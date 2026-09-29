/**
 * AWS DevOps Agent Demo Lab: the presenter's control room, as its own Cloudscape shell.
 *
 * Everything shown here is data: lab/scenarios.yaml (served by GET /admin/scenarios)
 * describes the cards, the skill and the notes; GET /admin/status says what is injected
 * right now (the demo's live probes, as facts) and where each engine run is (Lambda
 * durable function history). The page holds no scenario knowledge of its own.
 *
 * This is the EKS demo's Lab UI, and the reference implementation other demos adapt.
 * It is mounted by the portal's router behind its authentication; no router or auth
 * import here.
 */
import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import '@cloudscape-design/global-styles/index.css'
import Alert from '@cloudscape-design/components/alert'
import AppLayout from '@cloudscape-design/components/app-layout'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar'
import Header from '@cloudscape-design/components/header'
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs'
import SpaceBetween from '@cloudscape-design/components/space-between'
import TextContent from '@cloudscape-design/components/text-content'
import { AgentTask, Fact, LabEnvironment, ScenariosResponse, StatusResponse, UsageResponse, consoleLinks, formatCountdown, labApi } from './api'
import ScenarioCards, { deriveState, factToPair } from './ScenarioCards'
import { SkillPanel, TasksPanel, UsagePanel } from './AgentPanels'

const POLL_IDLE_MS = 10_000
const POLL_BUSY_MS = 3_000

const EMPTY_ENV: LabEnvironment = { region: '', partition: 'aws', devOpsAgentRegion: '', devOpsAgentSpaceId: '' }

export interface LabPageProps {
  /** Page title. Default: "AWS DevOps Agent Demo Lab". */
  title?: string
  /** One line under the title. Default names the demo environment generically. */
  tagline?: string
  /** Demo-owned buttons placed before the Lab's own in the header (a "Back to <app>" button, sign out). */
  actions?: ReactNode
}

export default function LabPage({ title = 'AWS DevOps Agent Demo Lab', tagline, actions }: LabPageProps) {
  const [definitions, setDefinitions] = useState<ScenariosResponse | null>(null)
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [usage, setUsage] = useState<UsageResponse | null>(null)
  const [tasks, setTasks] = useState<AgentTask[]>([])
  const [tasksLoading, setTasksLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [acting, setActing] = useState<string | null>(null)
  // Scenario whose rollback the engine has acknowledged but the status poll has not
  // reflected yet; cleared once the execution history shows the wait step resolved.
  const [rollbackRequested, setRollbackRequested] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ type: FlashbarProps.Type; content: string } | null>(null)
  // Auto-revert deadlines (epoch ms) per scenario, set from each status poll, ticked locally.
  const [deadlines, setDeadlines] = useState<Record<string, number | null>>({})
  const [now, setNow] = useState(Date.now())
  const statusInFlight = useRef(false)

  const fetchStatus = useCallback(async () => {
    if (statusInFlight.current) return
    statusInFlight.current = true
    try {
      const data = await labApi.status()
      setStatus(data)
      setError(null)
      setRollbackRequested(prev => {
        if (!prev) return prev
        const run = data.scenarios[prev]?.run
        const waiting = run?.status === 'RUNNING' && run.phases.find(p => p.id === 'await-rollback')?.status === 'in-progress'
        return waiting ? prev : null
      })
      const received = Date.now()
      setDeadlines(Object.fromEntries(Object.entries(data.scenarios).map(([id, st]) => {
        const left = st.run?.remainingSeconds
        return [id, left === null || left === undefined ? null : received + left * 1000]
      })))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      statusInFlight.current = false
    }
  }, [])

  const fetchAgentData = useCallback(async () => {
    setTasksLoading(true)
    const [u, l] = await Promise.allSettled([labApi.usage(), labApi.tasks()])
    if (u.status === 'fulfilled' && u.value.success) setUsage(u.value)
    if (l.status === 'fulfilled' && l.value.success) setTasks(l.value.tasks ?? [])
    setTasksLoading(false)
  }, [])

  useEffect(() => {
    labApi.scenarios().then(setDefinitions).catch(e => setError(e instanceof Error ? e.message : String(e)))
    fetchStatus()
    fetchAgentData()
  }, [fetchStatus, fetchAgentData])

  const busy = status?.busy ?? null
  useEffect(() => {
    const id = setInterval(fetchStatus, busy ? POLL_BUSY_MS : POLL_IDLE_MS)
    return () => clearInterval(id)
  }, [fetchStatus, busy])

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  const remaining = useMemo(() => Object.fromEntries(
    Object.entries(deadlines).map(([id, d]) => [id, d === null ? null : Math.max(0, Math.round((d - now) / 1000))]),
  ), [deadlines, now])

  const env = status?.environment ?? definitions?.environment ?? EMPTY_ENV
  const links = useMemo(() => consoleLinks(env), [env])
  const scenarios = definitions?.scenarios ?? []
  const statuses = status?.scenarios ?? {}
  const autoRevertMinutes = Math.round(Math.max(600, ...scenarios.map(s => s.autoRevertSeconds || 0)) / 60)
  // Region belongs with the demo's own facts (cluster, namespace, ...), it is not a card of its own.
  const envFacts: Fact[] = [...(env.facts ?? []), ...(env.region ? [{ label: 'Region', value: env.region }] : [])]

  async function act(id: string, kind: 'inject' | 'rollback') {
    setActing(id)
    setNotice(null)
    try {
      const res = kind === 'inject' ? await labApi.inject(id) : await labApi.rollback(id)
      setNotice({ type: res.success ? 'success' : 'error', content: res.message })
      // The engine acknowledged the rollback: show "reverting" now, not at the next poll.
      if (kind === 'rollback' && res.success) setRollbackRequested(id)
    } catch (e) {
      setNotice({ type: 'error', content: e instanceof Error ? e.message : String(e) })
    } finally {
      setActing(null)
      setTimeout(fetchStatus, 1500)
    }
  }

  // Sticky scope indicator: the whole page is about the injected scenario while one is active.
  const busyScenario = busy ? scenarios.find(s => s.id === busy) : undefined
  const derivedBusyState = busy ? deriveState(statuses[busy]) : 'healthy'
  const busyState = busy && rollbackRequested === busy && derivedBusyState === 'injected' ? 'reverting' : derivedBusyState
  // Not stacked: at most three messages, and the newest (the action's outcome) must be
  // visible right under the banner whose button was just clicked.
  const flashItems: FlashbarProps.MessageDefinition[] = []
  if (error) {
    flashItems.push({ id: 'error', type: 'error', header: 'Lab API unreachable', content: error })
  }
  if (busyScenario) {
    const left = remaining[busy!]
    flashItems.push({
      id: 'busy',
      type: busyState === 'reverting' ? 'in-progress' : 'warning',
      loading: busyState === 'injecting' || busyState === 'reverting',
      header: busyState === 'reverting' ? `${busyScenario.name}: reverting` : `${busyScenario.name} is injected`,
      content: busyState === 'reverting'
        ? 'The engine is putting the cluster back. Other scenarios unlock when it is done.'
        : `${left !== null && left !== undefined ? `Auto-revert in ${formatCountdown(left)}. ` : ''}Only one scenario runs at a time so the investigation stays unambiguous.`,
      action: busyState === 'injected'
        ? <Button onClick={() => act(busy!, 'rollback')} loading={acting === busy}>Rollback now</Button>
        : undefined,
    })
  }
  if (notice) {
    flashItems.push({ id: 'notice', type: notice.type, content: notice.content, dismissible: true, onDismiss: () => setNotice(null) })
  }

  const content = (
    <ContentLayout
      defaultPadding
      header={
        <Header
          variant="h1"
          description={tagline ?? 'Break the demo environment on purpose, watch the AWS DevOps Agent work on it, put it back.'}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              {actions}
              {links.devOpsAgent && <Button href={links.devOpsAgent} target="_blank" iconAlign="right" iconName="external">DevOps Agent console</Button>}
              <Button iconName="refresh" onClick={() => { fetchStatus(); fetchAgentData() }} ariaLabel="Refresh">Refresh</Button>
            </SpaceBetween>
          }
        >
          {title}
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Header
          variant="h2"
          description={`Inject a real infrastructure failure. Each injection is a Lambda durable function run: it reverts when you click Rollback, or on its own after ${autoRevertMinutes} minutes, even with this page closed.`}
        >
          Failure scenarios
        </Header>
        {envFacts.length > 0 && (
          <Container header={<Header variant="h3">Environment</Header>}>
            <KeyValuePairs columns={Math.min(envFacts.length + 1, 4)} items={envFacts.map(factToPair)} />
          </Container>
        )}
        <ScenarioCards
          scenarios={scenarios}
          statuses={statuses}
          links={links}
          busy={busy}
          acting={acting}
          rollbackRequested={rollbackRequested}
          remaining={remaining}
          loading={!definitions || !status}
          onInject={id => act(id, 'inject')}
          onRollback={id => act(id, 'rollback')}
        />
        {definitions && definitions.notes.length > 0 && (
          <Alert type="info" header="Before you present">
            <TextContent><ul>{definitions.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></TextContent>
          </Alert>
        )}

        {definitions?.skills.map(skill => (
          <SkillPanel key={skill.name} skill={skill} prompt={definitions.capability?.prompt} links={links} />
        ))}

        <TasksPanel tasks={tasks} links={links} loading={tasksLoading} />
        {usage && <UsagePanel usage={usage} />}
      </SpaceBetween>
    </ContentLayout>
  )

  // Own shell, separate from the demo's application: the presenter's control room.
  // No top bar (the page header carries the title and the demo's actions), no side
  // navigation, no tools panel, whole width, notifications sticky at the top.
  return (
    <AppLayout
      navigationHide
      toolsHide
      contentType="cards"
      maxContentWidth={Number.MAX_VALUE}
      notifications={<Flashbar items={flashItems} />}
      stickyNotifications
      content={content}
    />
  )
}
