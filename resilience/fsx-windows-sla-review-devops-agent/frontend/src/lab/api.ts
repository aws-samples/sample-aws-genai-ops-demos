/**
 * Lab API client and types.
 *
 * The Lab UI owns no scenario knowledge: everything it shows comes from GET /admin/scenarios
 * (lab/scenarios.yaml served by the Lab Lambda) and GET /admin/status (live probes of the
 * file system and its alarms + the engine's execution history). Same origin as the site:
 * CloudFront routes /admin/* to the Lab API and asks for Basic Auth once for both.
 */

const ADMIN_API_BASE = '/admin'

// CloudFront signs requests to the Lambda function URL (origin access control) and requires
// the viewer to state the payload hash on POST/DELETE. Our mutations carry no body.
const EMPTY_BODY_SHA256 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'

export type PhaseStatus = 'pending' | 'in-progress' | 'success' | 'error' | 'stopped'

export interface Scenario {
  id: string
  name: string
  category: string
  handler: string
  demonstrates?: { check?: string; withCapability?: string; withoutCapability?: string }
  /** Chat-driven scenarios: the sentence to paste in the agent's chat (shown on the card). */
  prompt?: string
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

/** The capability the Lab shows. `source: agent-tools` means it lives in the Agent Tools repository. */
export interface Skill {
  name: string
  source?: 'inline' | 'agent-tools'
  kind?: 'skill' | 'custom-agent'
  ref?: string
  agentType?: string
  feature?: string
  pitch?: string
  description?: string
  instructions?: string
  /** Is the skill registered in the Agent Space? Read live by the Lab API (Asset API). */
  registration?: SkillRegistration
}

export interface SkillRegistration {
  success: boolean
  found: boolean
  assetId?: string
  status?: string
  version?: number
  agentTypes?: string[]
  updatedAt?: string
  message?: string
}

export interface LabEnvironment {
  region: string
  partition: string
  devOpsAgentRegion: string
  devOpsAgentSpaceId: string
  /** Console URL of the alarm-to-webhook Lambda (the demo has a trigger chain). */
  triggerLambdaUrl?: string | null
  /** Facts about the environment as a whole (file system, deployment, domain controller). */
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

export type FactStatus = 'success' | 'error' | 'warning' | 'pending' | 'stopped' | 'in-progress' | 'info' | 'loading'

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

async function mutate(path: string, method: 'POST' | 'DELETE'): Promise<ActionResponse> {
  const res = await fetch(`${ADMIN_API_BASE}${path}`, { method, headers: { 'x-amz-content-sha256': EMPTY_BODY_SHA256 } })
  return res.json()
}

export const labApi = {
  scenarios: () => getJson<ScenariosResponse>('/scenarios'),
  status: () => getJson<StatusResponse>('/status'),
  usage: () => getJson<UsageResponse>('/usage'),
  tasks: () => getJson<{ success: boolean; tasks: AgentTask[] }>('/tasks'),
  inject: (id: string) => mutate(`/scenarios/${encodeURIComponent(id)}/inject`, 'POST'),
  rollback: (id: string) => mutate(`/scenarios/${encodeURIComponent(id)}/inject`, 'DELETE'),
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
  // Operator app paths: /knowledge?tab=skills lists the skills, /knowledge/skills/<assetId> is one skill.
  const app = devOpsAgentSpaceId ? `https://${devOpsAgentSpaceId}.aidevops.global.app.aws` : null
  return {
    triggerLambda: triggerLambdaUrl ?? null,
    devOpsAgent: app
      ? `${app}/`
      : devOpsAgentRegion ? `${host.replace(region, devOpsAgentRegion)}/aidevops/home#/agent-spaces` : null,
    chat: app ? `${app}/chat` : null,
    skills: app ? `${app}/knowledge?tab=skills` : null,
    skill: (assetId: string) => app ? `${app}/knowledge/skills/${assetId}` : null,
    investigation: (taskId: string) => app ? `${app}/${devOpsAgentSpaceId}/investigation/${taskId}` : null,
  }
}

export function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, '0')}`
}
