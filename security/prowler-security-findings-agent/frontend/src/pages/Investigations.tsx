import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Container,
  ContentLayout,
  ExpandableSection,
  Header,
  SpaceBetween,
  StatusIndicator,
  Table,
} from '@cloudscape-design/components';
import { InvestigationSummary, LiveSummary, listInvestigations } from '../api';
import { SEVERITY_ORDER } from '../theme';
import { AGENT_SKILLS } from '../agent-skills';
import { SeverityBadge } from '../ui';
import { hasSummary, isTaskRunning, taskState } from '../investigation-status';

// Loaded only when an investigation has a summary: it brings react-markdown.
const LiveSummaryList = lazy(() => import('../LiveSummary').then((m) => ({ default: m.LiveSummaryList })));

const STATUS_LABELS: Record<string, string> = {
  FAILED: 'Failed',
  ERROR: 'Failed',
  TIMED_OUT: 'Timed out',
  CANCELED: 'Canceled',
  CANCELLED: 'Canceled',
  SKIPPED: 'Skipped',
};

function statusIndicator(status?: string) {
  const s = (status || '').toUpperCase();
  if (s === 'COMPLETED') return <StatusIndicator type="success">Completed</StatusIndicator>;
  if (s === 'IN_PROGRESS' || s === 'RUNNING' || s === 'ACTIVE') return <StatusIndicator type="in-progress">In progress</StatusIndicator>;
  if (taskState(s) === 'failed') return <StatusIndicator type="error">{STATUS_LABELS[s]}</StatusIndicator>;
  if (taskState(s) === 'running') return <StatusIndicator type="pending">Pending</StatusIndicator>;
  return <StatusIndicator type="info">{status || 'unknown'}</StatusIndicator>;
}

function SkillField({
  label,
  value,
  multiline,
  maxHeight,
}: {
  label: string;
  value: string;
  multiline?: boolean;
  maxHeight?: number;
}) {
  return (
    <div>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--soc-fg-muted)', marginBottom: 4, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>{label}</span>
        <Button
          variant="inline-link"
          iconName="copy"
          onClick={() => navigator.clipboard.writeText(value)}
          ariaLabel={`Copy ${label}`}
        >
          Copy
        </Button>
      </div>
      <pre
        className="soc-code-block"
        style={{
          whiteSpace: multiline ? 'pre-wrap' : 'nowrap',
          margin: 0,
          ...(maxHeight ? { maxHeight, overflowY: 'auto' } : {}),
        }}
      >
        {value}
      </pre>
    </div>
  );
}

export default function Investigations() {
  const navigate = useNavigate();
  const [items, setItems] = useState<InvestigationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [agentSpaceId, setAgentSpaceId] = useState<string | undefined>();

  const load = useCallback(async (silent = false) => {
    if (!silent) {
      setLoading(true);
      setError(null);
    }
    try {
      const r = await listInvestigations();
      setItems(r.investigations || []);
      setAgentSpaceId(r.agentSpaceId);
      if (r.error && !silent) setError(r.error);
    } catch (e: any) {
      if (!silent) setError(e?.message || 'Failed to load investigations');
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const anyRunning = items.some((it) => isTaskRunning(it.status));
  useEffect(() => {
    if (!anyRunning) return;
    const id = window.setInterval(() => load(true), 10000);
    return () => window.clearInterval(id);
  }, [anyRunning, load]);

  const withSummary = items
    .filter((it): it is InvestigationSummary & { summary: LiveSummary } => hasSummary(it.summary))
    .sort((a, b) => Number(isTaskRunning(b.status)) - Number(isTaskRunning(a.status))
      || (b.summary.updatedAt || '').localeCompare(a.summary.updatedAt || ''));

  const operatorBase = agentSpaceId ? `https://${agentSpaceId}.aidevops.global.app.aws` : null;

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Every finding that's been dispatched to AWS DevOps Agent from this demo. Click a row to re-open the finding detail with the full agent journal."
          actions={<Button iconName="refresh" onClick={() => load()} loading={loading}>Refresh</Button>}
        >
          DevOps Agent investigations
        </Header>
      }
    >
      <SpaceBetween size="l">
        {error && (
          <Alert type="warning" dismissible onDismiss={() => setError(null)} header="DevOps Agent backlog is unavailable">
            {error === 'DEVOPS_AGENT_SPACE_ID not set' ? (
              <>
                This stack was deployed without a DevOps Agent Space ID, so there's nothing to list yet.
                Re-deploy with <code>-c devOpsAgentSpaceId=&lt;your-agent-space-id&gt;</code> (or export
                <code>DEVOPS_AGENT_SPACE_ID</code> before running <code>deploy-all.sh</code>) to wire it up.
              </>
            ) : error}
          </Alert>
        )}

        {withSummary.length > 0 && (
          <Suspense fallback={null}>
            <LiveSummaryList
              onFollow={navigate}
              items={withSummary.map((it) => ({
                id: it.finding_uid,
                heading: it.check_title || it.check_id || it.title || it.finding_uid,
                href: `/findings/${encodeURIComponent(it.finding_uid)}`,
                summary: it.summary,
                state: taskState(it.status),
              }))}
            />
          </Suspense>
        )}

        <Table
          loading={loading}
          loadingText="Querying DevOps Agent backlog…"
          items={items}
          trackBy="finding_uid"
          onRowClick={(e) => navigate(`/findings/${encodeURIComponent(e.detail.item.finding_uid)}`)}
          enableKeyboardNavigation
          header={<Header counter={`(${items.length})`} description="Click a row to open the finding and see the full agent journal.">All investigations</Header>}
          columnDefinitions={[
            {
              id: 'severity',
              header: 'Severity',
              cell: (it) => (it.severity
                ? <SeverityBadge severity={it.severity} />
                : <Box color="text-status-inactive" variant="small">—</Box>),
              sortingField: 'severity',
              sortingComparator: (a, b) => (SEVERITY_ORDER[a.severity ?? ''] ?? 99) - (SEVERITY_ORDER[b.severity ?? ''] ?? 99),
              width: 120,
            },
            {
              id: 'status',
              header: 'Agent status',
              cell: (it) => statusIndicator(it.status),
              sortingField: 'status',
              sortingComparator: (a, b) => (a.status || '').localeCompare(b.status || ''),
              width: 160,
            },
            {
              id: 'check',
              header: 'Check',
              cell: (it) => (
                <Box>
                  <Box fontWeight="bold">{it.check_title || it.check_id || it.title || '—'}</Box>
                  <Box variant="small" color="text-status-inactive">{it.check_id || it.finding_uid}</Box>
                </Box>
              ),
              sortingField: 'check',
              sortingComparator: (a, b) => (a.check_title || a.check_id || '').localeCompare(b.check_title || b.check_id || ''),
            },
            {
              id: 'operator',
              header: 'Agent Operator',
              cell: (it) => {
                if (!operatorBase) return <Box color="text-status-inactive" variant="small">—</Box>;
                const execId = it.executionId;
                const href = execId ? `${operatorBase}/investigation/${execId}` : `${operatorBase}/dashboard`;
                return (
                  <Button
                    href={href}
                    iconAlign="right"
                    iconName="external"
                    target="_blank"
                    rel="noopener noreferrer"
                    variant="inline-link"
                    ariaLabel={`Open investigation for ${it.check_title || it.check_id || 'finding'} in a new tab`}
                    onClick={(e) => { e.stopPropagation(); }}
                  >
                    Open
                  </Button>
                );
              },
              width: 160,
            },
            {
              id: 'service',
              header: 'Service',
              cell: (it) => it.service_name || '—',
              sortingField: 'service',
              sortingComparator: (a, b) => (a.service_name || '').localeCompare(b.service_name || ''),
              width: 140,
            },
            {
              id: 'resource',
              header: 'Resource',
              cell: (it) => (
                <Box variant="code">
                  <span translate="no" title={it.resource_uid}>
                    {(it.resource_uid || '').length > 60 ? (it.resource_uid || '').slice(0, 57) + '…' : (it.resource_uid || '—')}
                  </span>
                </Box>
              ),
              sortingField: 'resource',
              sortingComparator: (a, b) => (a.resource_uid || '').localeCompare(b.resource_uid || ''),
            },
            {
              id: 'updatedAt',
              header: 'Last updated',
              cell: (it) => (it.updatedAt ? new Date(it.updatedAt).toLocaleString() : (it.createdAt ? new Date(it.createdAt).toLocaleString() : '—')),
              sortingField: 'updatedAt',
              sortingComparator: (a, b) => (a.updatedAt || a.createdAt || '').localeCompare(b.updatedAt || b.createdAt || ''),
              width: 180,
            },
          ]}
          empty={
            <Box textAlign="center" padding={{ vertical: 'xl' }}>
              <SpaceBetween size="s">
                <Box variant="h3">No investigations yet</Box>
                <Box color="text-status-inactive">
                  Dispatch an investigation from the Findings page. The DevOps Agent will appear here as soon as it picks up the task.
                </Box>
                <Button variant="primary" onClick={() => navigate('/findings')}>Go to Findings</Button>
              </SpaceBetween>
            </Box>
          }
          stickyHeader
        />

        <Container
          header={
            <Header
              variant="h2"
              description="Copy-paste these ready-made Skills into the DevOps Agent console to make investigations from this demo sharper."
            >
              Seeded DevOps Agent Skills
            </Header>
          }
        >
          <SpaceBetween size="m">
            <Box variant="small" color="text-status-inactive">
              Skills can't be created programmatically yet (no public API) —
              open the DevOps Agent Operator, go to <strong>Skills → Create skill</strong>,
              and paste each field below into its matching form input
              (<code>Name</code>, <code>Description</code>, <code>Agent Type</code>,
              <code>Instructions</code>). Leave <code>Status</code> on
              <strong> Active</strong>.
            </Box>
            {AGENT_SKILLS.map((skill) => (
              <ExpandableSection
                key={skill.id}
                headerText={skill.title}
                variant="container"
              >
                <SpaceBetween size="s">
                  <Box variant="p">{skill.summary}</Box>
                  <SkillField label="Name" value={skill.name} />
                  <SkillField label="Description" value={skill.description} multiline />
                  <SkillField label="Agent Type" value={skill.agentType} />
                  <SkillField label="Instructions" value={skill.instructions} multiline maxHeight={260} />
                </SpaceBetween>
              </ExpandableSection>
            ))}
          </SpaceBetween>
        </Container>
      </SpaceBetween>
    </ContentLayout>
  );
}
