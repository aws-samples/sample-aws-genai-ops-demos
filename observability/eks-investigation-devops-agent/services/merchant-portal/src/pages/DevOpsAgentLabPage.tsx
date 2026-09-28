/**
 * DevOps Agent Lab: the presenter's control room.
 *
 * Everything shown here is data: lab/scenarios.yaml (served by GET /admin/scenarios)
 * describes the cards, the skill and the notes; GET /admin/status says what is
 * broken right now (live cluster probe) and where each engine run is (Lambda
 * durable function history). The page holds no scenario knowledge of its own.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import '@cloudscape-design/global-styles/index.css'
import Alert from '@cloudscape-design/components/alert'
import AppLayout from '@cloudscape-design/components/app-layout'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar'
import Header from '@cloudscape-design/components/header'
import SpaceBetween from '@cloudscape-design/components/space-between'
import TextContent from '@cloudscape-design/components/text-content'
import TopNavigation from '@cloudscape-design/components/top-navigation'
import { useAuth } from '../context/AuthContext'
import { AgentTask, ScenariosResponse, StatusResponse, UsageResponse, consoleLinks, formatCountdown, labApi } from '../lab/api'
import ScenarioCards, { deriveState } from '../lab/ScenarioCards'
import { SkillPanel, TasksPanel, UsagePanel } from '../lab/AgentPanels'

const POLL_IDLE_MS = 10_000
const POLL_BUSY_MS = 3_000

const EMPTY_ENV = { region: '', partition: 'aws', clusterName: '', namespace: '', triggerLambdaName: '', devOpsAgentRegion: '', devOpsAgentSpaceId: '' }

export default function DevOpsAgentLabPage() {
  const navigate = useNavigate()
  const { user, logout } = useAuth()
  const [definitions, setDefinitions] = useState<ScenariosResponse | null>(null)
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [usage, setUsage] = useState<UsageResponse | null>(null)
  const [tasks, setTasks] = useState<AgentTask[]>([])
  const [tasksLoading, setTasksLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [acting, setActing] = useState<string | null>(null)
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

  async function act(id: string, kind: 'inject' | 'rollback') {
    setActing(id)
    setNotice(null)
    try {
      const res = kind === 'inject' ? await labApi.inject(id) : await labApi.rollback(id)
      setNotice({ type: res.success ? 'success' : 'error', content: res.message })
    } catch (e) {
      setNotice({ type: 'error', content: e instanceof Error ? e.message : String(e) })
    } finally {
      setActing(null)
      setTimeout(fetchStatus, 1500)
    }
  }

  // Sticky scope indicator: the whole page is about the injected scenario while one is active.
  const busyScenario = busy ? scenarios.find(s => s.id === busy) : undefined
  const busyState = busy ? deriveState(statuses[busy]) : 'healthy'
  const flashItems: FlashbarProps.MessageDefinition[] = []
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
  if (error) {
    flashItems.push({ id: 'error', type: 'error', header: 'Lab API unreachable', content: error })
  }

  const content = (
    <ContentLayout
      defaultPadding
      header={
        <Header
          variant="h1"
          description="Break the Helios platform on purpose, watch the AWS DevOps Agent investigate, put it back."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              {links.devOpsAgent && <Button href={links.devOpsAgent} target="_blank" iconAlign="right" iconName="external">DevOps Agent console</Button>}
              <Button iconName="refresh" onClick={() => { fetchStatus(); fetchAgentData() }} ariaLabel="Refresh">Refresh</Button>
            </SpaceBetween>
          }
        >
          AWS DevOps Agent Demo Lab
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Header
          variant="h2"
          description={`Inject a real infrastructure failure. Each injection is a Lambda durable function run: it reverts when you click Rollback, or on its own after ${autoRevertMinutes} minutes, even with this page closed.${env.clusterName ? ` Cluster ${env.clusterName} in ${env.region}.` : ''}`}
        >
          Failure scenarios
        </Header>
        <ScenarioCards
          scenarios={scenarios}
          statuses={statuses}
          links={links}
          busy={busy}
          acting={acting}
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

  // Own shell, separate from the Helios storefront: the presenter's control room.
  // Same layout choices as the lifecycle tracker: no side navigation, no tools panel,
  // whole width (no max content width), notifications sticky at the top.
  return (
    <>
      <TopNavigation
        identity={{ href: '/lab', title: 'AWS DevOps Agent Demo Lab', onFollow: e => { e.preventDefault(); navigate('/lab') } }}
        utilities={[
          { type: 'button', text: 'Back to Helios', iconName: 'arrow-left', onClick: () => navigate('/catalog') },
          ...(links.devOpsAgent ? [{ type: 'button' as const, text: 'DevOps Agent console', href: links.devOpsAgent, external: true, externalIconAriaLabel: '(opens in a new tab)' }] : []),
          {
            type: 'menu-dropdown',
            text: user?.signInDetails?.loginId ?? 'Account',
            iconName: 'user-profile',
            items: [{ id: 'signout', text: 'Sign out' }],
            onItemClick: async ({ detail }) => { if (detail.id === 'signout') { await logout(); navigate('/login') } },
          },
        ]}
        i18nStrings={{ overflowMenuTriggerText: 'More', overflowMenuTitleText: 'All' }}
      />
      <AppLayout
        navigationHide
        toolsHide
        contentType="cards"
        maxContentWidth={Number.MAX_VALUE}
        notifications={<Flashbar items={flashItems} stackItems />}
        stickyNotifications
        content={content}
      />
    </>
  )
}
