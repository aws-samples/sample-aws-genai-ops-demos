import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Badge from '@cloudscape-design/components/badge'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import CopyToClipboard from '@cloudscape-design/components/copy-to-clipboard'
import ExpandableSection from '@cloudscape-design/components/expandable-section'
import Header from '@cloudscape-design/components/header'
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs'
import Link from '@cloudscape-design/components/link'
import SpaceBetween from '@cloudscape-design/components/space-between'
import StatusIndicator, { StatusIndicatorProps } from '@cloudscape-design/components/status-indicator'
import Table from '@cloudscape-design/components/table'
import TextContent from '@cloudscape-design/components/text-content'
import { AgentTask, Skill, UsageResponse, consoleLinks } from './api'

// The agent's summaries are Markdown written as standalone documents (they open with
// an H1). Inside a card, demote headings two levels and let TextContent set the
// typography, so the summary reads as one block instead of a page-sized headline.
const SUMMARY_COMPONENTS: Components = {
  h1: ({ children }) => <h3>{children}</h3>,
  h2: ({ children }) => <h4>{children}</h4>,
  h3: ({ children }) => <h5>{children}</h5>,
  h4: ({ children }) => <h5>{children}</h5>,
  a: ({ href, children }) => <Link external href={href}>{children}</Link>,
}

export function AgentMarkdown({ children }: { children: string }) {
  return (
    <TextContent>
      <Markdown remarkPlugins={[remarkGfm]} components={SUMMARY_COMPONENTS}>{children}</Markdown>
    </TextContent>
  )
}

// ---------------------------------------------------------------------------
// Skill: the presenter creates it by copy-paste in the DevOps Agent console
// ---------------------------------------------------------------------------

const AGENT_TOOLS_REPO = 'https://github.com/aws/tools-for-devops-agent'

/** Inline skill: the presenter creates it in the operator app by copy-paste (name, description, instructions). */
function InlineSkillFields({ skill }: { skill: Skill }) {
  return (
    <>
      <KeyValuePairs
        columns={1}
        items={[
          { label: 'Name', value: <CopyToClipboard variant="inline" textToCopy={skill.name} copyButtonAriaLabel="Copy skill name" copySuccessText="Name copied" copyErrorText="Copy failed" /> },
        ]}
      />
      <KeyValuePairs
        columns={1}
        items={[
          {
            label: 'Description',
            value: (
              <SpaceBetween size="xs">
                <Box variant="p">{skill.description}</Box>
                <CopyToClipboard variant="button" textToCopy={skill.description ?? ''} copyButtonText="Copy description" copySuccessText="Description copied" copyErrorText="Copy failed" />
              </SpaceBetween>
            ),
          },
        ]}
      />
      <ExpandableSection headerText="Instructions" headerActions={
        <CopyToClipboard variant="button" textToCopy={skill.instructions ?? ''} copyButtonText="Copy instructions" copySuccessText="Instructions copied" copyErrorText="Copy failed" />
      }>
        <Box variant="code">
          <pre style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: '0.85em' }}>{skill.instructions}</pre>
        </Box>
      </ExpandableSection>
    </>
  )
}

/**
 * Referenced skill that the deploy did NOT register: the one command that fetches, packages
 * and registers it (deploy-skill with the Agent Space id), and the console upload as a fallback.
 */
function AgentToolsInstallFields({ skill, agentSpaceId }: { skill: Skill; agentSpaceId: string }) {
  const ref = skill.ref ?? 'main'
  const customAgent = skill.kind === 'custom-agent'
  const kind = customAgent ? 'CustomAgent' : 'Skill'
  const space = agentSpaceId ? ` -AgentSpaceId ${agentSpaceId}` : ''
  const spaceSh = agentSpaceId ? ` --agent-space-id ${agentSpaceId}` : ''
  const command = `& "..\\..\\shared\\devops-agent\\agent-tools\\deploy-skill.ps1" -${kind} ${skill.name} -Ref ${ref}${space}`
  const commandSh = `../../shared/devops-agent/agent-tools/deploy-skill.sh --${customAgent ? 'custom-agent' : 'skill'} ${skill.name} --ref ${ref}${spaceSh}`
  return (
    <KeyValuePairs
      columns={1}
      items={[
        {
          label: 'Register it (run from the demo folder; the deploy script does this on its own)',
          value: (
            <SpaceBetween size="xs">
              <Box variant="code">{command}</Box>
              <SpaceBetween direction="horizontal" size="xs">
                <CopyToClipboard variant="button" textToCopy={command} copyButtonText="Copy PowerShell" copySuccessText="Copied" copyErrorText="Copy failed" />
                <CopyToClipboard variant="button" textToCopy={commandSh} copyButtonText="Copy Bash" copySuccessText="Copied" copyErrorText="Copy failed" />
              </SpaceBetween>
              <Box color="text-body-secondary" fontSize="body-s">
                Fetches the skill at the ref, packages it and registers it in this Agent Space through the Asset API. Fallback: upload the zip it produces in the operator app (Knowledge, Skills). The skill stays in its repository; the demo references it.
              </Box>
            </SpaceBetween>
          ),
        },
      ]}
    />
  )
}

const REGISTRATION_STATUS: Record<string, StatusIndicatorProps.Type> = { ACTIVE: 'success', INACTIVE: 'stopped' }

/**
 * What the demo showcases, as its STATE in the Agent Space (read live through the Asset API),
 * not as installation instructions: the deploy registers the skill, so a registered skill is
 * the normal case and needs nothing from the presenter. When it is missing, the panel turns
 * into the prerequisite (rendered at the top of the page by LabPage) and shows the fix.
 */
export function SkillPanel({ skill, links, agentSpaceId }: { skill: Skill; links: ReturnType<typeof consoleLinks>; agentSpaceId: string }) {
  const referenced = skill.source === 'agent-tools'
  const reg = skill.registration
  const found = reg?.found === true
  const unknown = !reg || !reg.success
  const ref = skill.ref ?? 'main'
  const sourceUrl = referenced
    ? `${AGENT_TOOLS_REPO}/tree/${ref}/${skill.kind === 'custom-agent' ? 'custom-agents' : 'skills'}/${skill.name}`
    : undefined
  const skillUrl = found && reg?.assetId ? links.skill(reg.assetId) : null

  const registration = found
    ? <StatusIndicator type={REGISTRATION_STATUS[reg?.status ?? ''] ?? 'info'}>{reg?.status === 'ACTIVE' ? 'Registered, active' : `Registered, ${(reg?.status ?? '').toLowerCase()}`}</StatusIndicator>
    : unknown
      ? <StatusIndicator type="warning">Unknown{reg?.message ? `: ${reg.message}` : ''}</StatusIndicator>
      : <StatusIndicator type="error">Not registered in this Agent Space</StatusIndicator>

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={found
            ? (referenced
              ? 'Skills teach the agent a domain. This one comes from the public Agent Tools repository; the deploy registered it, the demo never copies it.'
              : "Skills encode your team's reporting standards. They load automatically and change how the agent reasons and reports.")
            : 'The scenarios below demonstrate this skill. Until it is registered in the Agent Space, the agent answers without it.'}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              {skillUrl
                ? <Button href={skillUrl} iconAlign="right" iconName="external" target="_blank">Open the skill in the operator app</Button>
                : links.skills && <Button href={links.skills} iconAlign="right" iconName="external" target="_blank">Open Skills in the operator app</Button>}
            </SpaceBetween>
          }
        >
          {found ? 'Agent skill' : 'Agent skill: prerequisite'}
        </Header>
      }
    >
      <SpaceBetween size="l">
        <KeyValuePairs
          columns={4}
          items={[
            { label: 'Skill', value: sourceUrl ? <Link external href={sourceUrl}>{skill.name}</Link> : skill.name },
            { label: 'Registration', value: registration },
            ...(found ? [{ label: 'Version', value: `${reg?.version ?? '?'}${reg?.updatedAt ? `, ${new Date(reg.updatedAt).toLocaleString()}` : ''}` }] : []),
            { label: 'Agent types', value: found && reg?.agentTypes?.length ? reg.agentTypes.join(', ') : (skill.agentType ?? 'Generic') },
          ]}
        />
        <Box variant="p">{skill.pitch}</Box>
        {skill.feature && (
          <Box color="text-body-secondary" fontSize="body-s">Showcases: <strong>{skill.feature}</strong></Box>
        )}
        {referenced
          ? (!found && !unknown && <AgentToolsInstallFields skill={skill} agentSpaceId={agentSpaceId} />)
          : (found
            ? <ExpandableSection headerText="Skill definition (already registered)"><SpaceBetween size="l"><InlineSkillFields skill={skill} /></SpaceBetween></ExpandableSection>
            : <InlineSkillFields skill={skill} />)}
      </SpaceBetween>
    </Container>
  )
}

// ---------------------------------------------------------------------------
// Agent tasks: what the agent did in the Agent Space, with execution facts
// ---------------------------------------------------------------------------

const TASK_STATUS: Record<string, StatusIndicatorProps.Type> = {
  COMPLETED: 'success', IN_PROGRESS: 'in-progress', FAILED: 'error', PENDING: 'pending',
}

function duration(log: AgentTask): string {
  const start = new Date(log.createdAt).getTime()
  const end = new Date(log.updatedAt || log.createdAt).getTime()
  const s = Math.max(0, Math.round((end - start) / 1000))
  return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`
}

export function TasksPanel({ tasks, links, loading }: { tasks: AgentTask[]; links: ReturnType<typeof consoleLinks>; loading: boolean }) {
  return (
    <Table
      variant="container"
      loading={loading}
      loadingText="Loading agent tasks"
      items={tasks}
      trackBy="taskId"
      header={
        <Header
          variant="h2"
          counter={`(${tasks.length})`}
          // Chats are per-user executions, not backlog tasks: the Lab cannot list them, the operator app does.
          description="What the DevOps Agent has done in this Agent Space, from its backlog: investigations, evaluations and system learning. Chats are per user and live in the operator app."
          actions={links.chat && <Button href={links.chat} iconAlign="right" iconName="external" target="_blank">Your chats in the operator app</Button>}
        >
          Agent tasks
        </Header>
      }
      empty={<Box textAlign="center" color="inherit">No tasks yet. Inject an alarm-driven scenario to start an investigation.</Box>}
      columnDefinitions={[
        { id: 'status', header: 'Status', cell: l => <StatusIndicator type={TASK_STATUS[l.status] ?? 'info'}>{l.status}</StatusIndicator> },
        {
          id: 'title', header: 'Task', cell: l => {
            const href = links.investigation(l.taskId)
            return href ? <Link external href={href}>{l.title || l.taskId}</Link> : (l.title || l.taskId)
          },
        },
        { id: 'type', header: 'Type', cell: l => <SpaceBetween direction="horizontal" size="xxs"><Badge>{l.taskType}</Badge>{l.priority && <Badge color="grey">{l.priority}</Badge>}</SpaceBetween> },
        { id: 'created', header: 'Created', cell: l => new Date(l.createdAt).toLocaleString() },
        { id: 'duration', header: 'Duration', cell: duration },
        { id: 'tools', header: 'Tool calls', cell: l => l.toolCalls ?? '–' },
        { id: 'skills', header: 'Skills loaded', cell: l => l.skillNames && l.skillNames.length > 0 ? l.skillNames.join(', ') : <Box color="text-status-inactive">none</Box> },
      ]}
      footer={tasks.some(l => l.summaryMd) && (
        <ExpandableSection headerText="Task summaries">
          <SpaceBetween size="m">
            {tasks.filter(l => l.summaryMd).map(l => (
              <ExpandableSection key={l.taskId} headerText={l.title || l.taskId} variant="footer">
                <AgentMarkdown>{l.summaryMd!}</AgentMarkdown>
              </ExpandableSection>
            ))}
          </SpaceBetween>
        </ExpandableSection>
      )}
    />
  )
}

// ---------------------------------------------------------------------------
// Spend: what the agent cost this month, by kind of work. Quotas are ignored
// (the usage API reports limit -1 when none is set; nobody presents on quotas).
// ---------------------------------------------------------------------------

const AGENT_SECOND_PRICE_USD = 0.0083

function spendLine(hours: number): string {
  const seconds = Math.round(hours * 3600)
  return `$${(seconds * AGENT_SECOND_PRICE_USD).toFixed(2)} (${seconds} agent-seconds at $${AGENT_SECOND_PRICE_USD}/s, list price)`
}

export function UsagePanel({ usage }: { usage: UsageResponse }) {
  const motions = [
    { label: 'Investigation', hours: usage.monthlyAccountInvestigationHours?.usage },
    { label: 'Evaluation', hours: usage.monthlyAccountEvaluationHours?.usage },
    { label: 'On-demand (chat)', hours: usage.monthlyAccountOnDemandHours?.usage },
    { label: 'System learning', hours: usage.monthlyAccountSystemLearningHours?.usage },
  ].filter((m): m is { label: string; hours: number } => typeof m.hours === 'number')
  const totalHours = motions.reduce((acc, m) => acc + m.hours, 0)
  return (
    <Container header={<Header variant="h2">Agent spend</Header>}>
      <KeyValuePairs
        columns={1}
        items={[
          { label: 'Estimated spend this month', value: <Box variant="strong">{spendLine(totalHours)}</Box> },
          ...motions.map(m => ({ label: m.label, value: spendLine(m.hours) })),
        ]}
      />
    </Container>
  )
}
