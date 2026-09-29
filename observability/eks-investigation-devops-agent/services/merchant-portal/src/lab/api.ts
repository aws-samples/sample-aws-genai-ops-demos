/**
 * DevOps Agent Lab API client and types.
 *
 * The Lab UI owns no scenario knowledge: everything it shows comes from
 * GET /admin/scenarios (lab/scenarios.yaml served by the Lab Lambda) and
 * GET /admin/status (live cluster probes + the engine's execution history).
 */

const ADMIN_API_BASE = '/admin'

export type PhaseStatus = 'pending' | 'in-progress' | 'success' | 'error' | 'stopped'

export interface Scenario {
  id: string
  name: string
  category: string
  handler: string
  demonstrates?: { check?: string; withCapability?: string; withoutCapability?: string }
  triggersAlarm?: boolean
  alarm?: { envVar?: string; expectFiringWithinSeconds?: number }
  alarmName?: string
  inject?: { summary?: string }
  detect?: { summary?: string }
  autoRevertSeconds: number
  description?: string
  incidentChain?: string[]
  customerImpact?: string[]
  demoFlow?: string[]
  talkTrack?: string
}

export interface Skill {
  name: string
  agentType?: string
  feature?: string
  pitch?: string
  description: string
  instructions: string
}

export interface LabEnvironment {
  region: string
  partition: string
  devOpsAgentRegion: string
  devOpsAgentSpaceId: string
  /** Console URL of the alarm-to-webhook Lambda, when the demo has a trigger chain. */
  triggerLambdaUrl?: string | null
  /** Demo-provided facts about the environment as a whole (cluster, namespace, ...). */
  facts?: Fact[]
}

export interface ScenariosResponse {
  success: boolean
  schemaVersion: number
  capability?: { kind?: string; name?: string; source?: string; agentTypes?: string[]; prompt?: string }
  scenarios: Scenario[]
  skills: Skill[]
  notes: string[]
  environment: LabEnvironment
}

/** Cloudscape StatusIndicator types the backend may use on a fact. */
export type FactStatus = 'success' | 'error' | 'warning' | 'pending' | 'stopped' | 'in-progress' | 'info' | 'loading'

/**
 * A labelled value returned by a scenario's probe. The UI renders facts without knowing
 * the domain: `value` (with optional status and detail), or `items` (a list of them), or
 * `progress` (a bar). `link` becomes the key/value pair's info link.
 */
export interface FactItem { text: string; status?: FactStatus; detail?: string }
export interface Fact {
  label: string
  value?: string
  items?: FactItem[]
  status?: FactStatus
  detail?: string
  progress?: { percent: number; text: string }
  link?: { text: string; href: string }
}

export interface Run {
  executionArn: string
  executionName?: string
  status: 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'TIMED_OUT' | 'STOPPED' | string
  startedAt?: number | null
  endedAt?: number | null
  phases: Array<{ id: string; label: string; status: PhaseStatus }>
  remainingSeconds?: number | null
  revertReason?: 'manual' | 'auto' | null
}

export interface ScenarioStatus {
  injected: boolean
  error?: string
  facts: Fact[]
  run?: Run
  lastRun?: Run
}

export interface StatusResponse {
  success: boolean
  busy: string | null
  scenarios: Record<string, ScenarioStatus>
  environment: LabEnvironment
  checkedAt: number
}

export interface ActionResponse { success: boolean; message: string; [key: string]: unknown }

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${ADMIN_API_BASE}${path}`)
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`)
  return res.json()
}

export const labApi = {
  scenarios: () => getJson<ScenariosResponse>('/scenarios'),
  status: () => getJson<StatusResponse>('/status'),
  usage: () => getJson<UsageResponse>('/usage'),
  tasks: () => getJson<{ success: boolean; tasks: AgentTask[] }>('/tasks'),
  inject: async (id: string): Promise<ActionResponse> => {
    const res = await fetch(`${ADMIN_API_BASE}/scenarios/${encodeURIComponent(id)}/inject`, { method: 'POST' })
    return res.json()
  },
  rollback: async (id: string): Promise<ActionResponse> => {
    const res = await fetch(`${ADMIN_API_BASE}/scenarios/${encodeURIComponent(id)}/inject`, { method: 'DELETE' })
    return res.json()
  },
}

export interface UsageBucket { usage: number; limit: number }
export interface UsageResponse {
  success: boolean
  monthlyAccountInvestigationHours?: UsageBucket
  monthlyAccountEvaluationHours?: UsageBucket
  monthlyAccountOnDemandHours?: UsageBucket
  monthlyAccountSystemLearningHours?: UsageBucket
}

export interface AgentTask {
  taskId: string
  title: string
  status: string
  priority: string
  taskType: string
  createdAt: string
  updatedAt: string
  executionId?: string
  agentType?: string
  toolCalls?: number
  skillReads?: number
  skillNames?: string[]
  summaryMd?: string
  journalRecordCount?: number
}

/** Console host for the deployment's partition (never a literal region). */
export function consoleHost(env: Pick<LabEnvironment, 'region' | 'partition'>): string {
  const { region, partition } = env
  if (partition === 'aws-cn') return `https://${region}.console.amazonaws.cn`
  if (partition === 'aws-us-gov') return `https://${region}.console.amazonaws-us-gov.com`
  return `https://${region}.console.aws.amazon.com`
}

/** Links the Lab itself owns: the agent's console and the trigger chain. Everything
 *  environment-specific arrives as a fact with its own link. */
export function consoleLinks(env: LabEnvironment) {
  const host = consoleHost(env)
  const { region, devOpsAgentRegion, devOpsAgentSpaceId, triggerLambdaUrl } = env
  return {
    triggerLambda: triggerLambdaUrl ?? null,
    devOpsAgent: devOpsAgentSpaceId
      ? `https://${devOpsAgentSpaceId}.aidevops.global.app.aws/`
      : devOpsAgentRegion ? `${host.replace(region, devOpsAgentRegion)}/aidevops/home#/agent-spaces` : null,
    skills: devOpsAgentSpaceId ? `https://${devOpsAgentSpaceId}.aidevops.global.app.aws/skills` : null,
    investigation: (taskId: string) => devOpsAgentSpaceId
      ? `https://${devOpsAgentSpaceId}.aidevops.global.app.aws/${devOpsAgentSpaceId}/investigation/${taskId}` : null,
  }
}

export function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, '0')}`
}
