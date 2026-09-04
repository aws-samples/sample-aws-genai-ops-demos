import { useState, useEffect } from 'react';
import {
  Table,
  Header,
  SpaceBetween,
  Box,
  Button,
  Modal,
  FormField,
  Input,
  Select,
  SelectProps,
  Badge,
  Spinner,
  Alert,
  Container,
  ColumnLayout,
} from '@cloudscape-design/components';
import { UserInfo } from '../App';
import { ScopingRule, Team } from '../types';
import { getScopingRules, createScopingRule, deleteScopingRule, getTeams } from '../services/api';

interface ScopingRulesProps {
  userInfo: UserInfo | null;
}

const RULE_TYPES: SelectProps.Option[] = [
  { value: 'accountId', label: 'Account ID', description: 'Match by AWS account ID' },
  { value: 'resourceTag', label: 'Resource Tag', description: 'Match by resource tag pattern (key=value)' },
  { value: 'serviceName', label: 'Service Name', description: 'Match by AWS service name' },
  { value: 'costCenter', label: 'Cost Center', description: 'Match by cost center tag' },
];

const TYPE_COLORS: Record<string, 'blue' | 'green' | 'grey' | 'red'> = {
  accountId: 'blue',
  resourceTag: 'green',
  serviceName: 'grey',
  costCenter: 'red',
};

interface RuleFormData {
  type: SelectProps.Option | null;
  pattern: string;
  teamId: SelectProps.Option | null;
  description: string;
  priority: string;
}

const initialFormData: RuleFormData = {
  type: null,
  pattern: '',
  teamId: null,
  description: '',
  priority: '100',
};

export default function ScopingRules({ userInfo }: ScopingRulesProps) {
  const [rules, setRules] = useState<ScopingRule[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  
  // Modal state
  const [showModal, setShowModal] = useState(false);
  const [formData, setFormData] = useState<RuleFormData>(initialFormData);
  
  // Delete confirmation
  const [deleteConfirm, setDeleteConfirm] = useState<ScopingRule | null>(null);
  
  // Result notification
  const [actionResult, setActionResult] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const isAdmin = userInfo?.groups.includes('finops-admin');

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    setLoading(true);
    try {
      const [rulesData, teamsData] = await Promise.all([
        getScopingRules(),
        getTeams(),
      ]);
      setRules(rulesData.rules);
      setTeams(teamsData.teams);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data');
    } finally {
      setLoading(false);
    }
  }

  const teamOptions: SelectProps.Option[] = teams.map(t => ({
    value: t.teamId,
    label: t.name,
  }));

  const openCreateModal = () => {
    setFormData(initialFormData);
    setShowModal(true);
  };

  const handleSave = async () => {
    if (!formData.type || !formData.pattern || !formData.teamId) return;
    
    setActionLoading(true);
    try {
      await createScopingRule({
        type: formData.type.value || '',
        pattern: formData.pattern,
        teamId: formData.teamId.value || '',
        description: formData.description || undefined,
        priority: parseInt(formData.priority) || 100,
      });
      setActionResult({ type: 'success', message: 'Scoping rule created successfully' });
      setShowModal(false);
      loadData();
    } catch (err) {
      setActionResult({
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to create rule',
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteConfirm) return;
    setActionLoading(true);
    try {
      await deleteScopingRule(deleteConfirm.ruleId);
      setActionResult({ type: 'success', message: 'Scoping rule deleted successfully' });
      setDeleteConfirm(null);
      loadData();
    } catch (err) {
      setActionResult({
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to delete rule',
      });
    } finally {
      setActionLoading(false);
    }
  };

  const getTeamName = (teamId: string) => {
    const team = teams.find(t => t.teamId === teamId);
    return team?.name || teamId;
  };

  if (loading) {
    return (
      <Box textAlign="center" padding="xxl">
        <Spinner size="large" />
        <Box variant="p" margin={{ top: 's' }}>Loading scoping rules...</Box>
      </Box>
    );
  }

  if (error) {
    return (
      <Alert type="error" header="Error loading scoping rules">
        {error}
      </Alert>
    );
  }

  return (
    <SpaceBetween size="l">
      {actionResult && (
        <Alert
          type={actionResult.type}
          dismissible
          onDismiss={() => setActionResult(null)}
        >
          {actionResult.message}
        </Alert>
      )}

      <Header
        variant="h1"
        description="Define rules to automatically assign findings to teams based on account, tags, or services"
        counter={`(${rules.length})`}
        actions={
          isAdmin && (
            <Button variant="primary" onClick={openCreateModal}>
              Create Rule
            </Button>
          )
        }
      >
        Scoping Rules
      </Header>

      <Container header={<Header variant="h2">How Scoping Works</Header>}>
        <ColumnLayout columns={4} variant="text-grid">
          <Box>
            <Badge color="blue">Account ID</Badge>
            <Box variant="p" margin={{ top: 'xs' }}>
              Match findings by AWS account ID. Use exact account IDs.
            </Box>
          </Box>
          <Box>
            <Badge color="green">Resource Tag</Badge>
            <Box variant="p" margin={{ top: 'xs' }}>
              Match by tag patterns like <code>team=platform</code> or <code>env=prod</code>.
            </Box>
          </Box>
          <Box>
            <Badge color="grey">Service Name</Badge>
            <Box variant="p" margin={{ top: 'xs' }}>
              Match by AWS service name (e.g., EC2, RDS, Lambda).
            </Box>
          </Box>
          <Box>
            <Badge color="red">Cost Center</Badge>
            <Box variant="p" margin={{ top: 'xs' }}>
              Match by cost allocation tag value.
            </Box>
          </Box>
        </ColumnLayout>
        <Box variant="p" margin={{ top: 'm' }} color="text-body-secondary">
          Rules are evaluated in priority order (lower number = higher priority). The first matching rule assigns the finding to that team.
        </Box>
      </Container>

      <Table
        columnDefinitions={[
          {
            id: 'priority',
            header: 'Priority',
            cell: item => <Badge>{item.priority}</Badge>,
            width: 90,
            sortingField: 'priority',
          },
          {
            id: 'type',
            header: 'Type',
            cell: item => (
              <Badge color={TYPE_COLORS[item.type]}>
                {RULE_TYPES.find(t => t.value === item.type)?.label || item.type}
              </Badge>
            ),
            width: 130,
          },
          {
            id: 'pattern',
            header: 'Pattern',
            cell: item => <code>{item.pattern}</code>,
            sortingField: 'pattern',
          },
          {
            id: 'team',
            header: 'Assigned Team',
            cell: item => getTeamName(item.teamId),
            width: 180,
          },
          {
            id: 'description',
            header: 'Description',
            cell: item => item.description || '-',
          },
          {
            id: 'created',
            header: 'Created',
            cell: item => new Date(item.createdAt).toLocaleDateString(),
            width: 110,
          },
          {
            id: 'actions',
            header: 'Actions',
            cell: item => (
              isAdmin && (
                <Button variant="link" onClick={() => setDeleteConfirm(item)}>
                  Delete
                </Button>
              )
            ),
            width: 100,
          },
        ]}
        items={rules.sort((a, b) => a.priority - b.priority)}
        loading={loading}
        loadingText="Loading scoping rules..."
        empty={
          <Box textAlign="center" padding="l">
            <Box variant="h3">No scoping rules</Box>
            <Box variant="p">Create rules to automatically assign findings to teams.</Box>
            {isAdmin && (
              <Button onClick={openCreateModal}>Create Rule</Button>
            )}
          </Box>
        }
        variant="full-page"
        stickyHeader
        sortingDisabled
      />

      {/* Create Modal */}
      <Modal
        visible={showModal}
        onDismiss={() => setShowModal(false)}
        header="Create Scoping Rule"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setShowModal(false)}>Cancel</Button>
              <Button
                variant="primary"
                onClick={handleSave}
                loading={actionLoading}
                disabled={!formData.type || !formData.pattern || !formData.teamId}
              >
                Create Rule
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Rule Type" constraintText="Required">
            <Select
              selectedOption={formData.type}
              onChange={({ detail }) => setFormData({ ...formData, type: detail.selectedOption })}
              options={RULE_TYPES}
              placeholder="Select rule type"
            />
          </FormField>
          
          <FormField
            label="Pattern"
            constraintText="Required"
            description={
              formData.type?.value === 'accountId' ? 'Enter the AWS account ID (e.g., 123456789012)' :
              formData.type?.value === 'resourceTag' ? 'Enter tag pattern (e.g., team=platform or env=prod)' :
              formData.type?.value === 'serviceName' ? 'Enter service name (e.g., EC2, RDS, Lambda)' :
              formData.type?.value === 'costCenter' ? 'Enter cost center value (e.g., CC-12345)' :
              'Select a rule type first'
            }
          >
            <Input
              value={formData.pattern}
              onChange={({ detail }) => setFormData({ ...formData, pattern: detail.value })}
              placeholder={
                formData.type?.value === 'accountId' ? '123456789012' :
                formData.type?.value === 'resourceTag' ? 'team=platform' :
                formData.type?.value === 'serviceName' ? 'EC2' :
                'Enter pattern'
              }
            />
          </FormField>
          
          <FormField label="Assign to Team" constraintText="Required">
            <Select
              selectedOption={formData.teamId}
              onChange={({ detail }) => setFormData({ ...formData, teamId: detail.selectedOption })}
              options={teamOptions}
              placeholder="Select team"
              empty="No teams available. Create a team first."
            />
          </FormField>
          
          <FormField
            label="Priority"
            description="Lower numbers are evaluated first (default: 100)"
          >
            <Input
              type="number"
              value={formData.priority}
              onChange={({ detail }) => setFormData({ ...formData, priority: detail.value })}
            />
          </FormField>
          
          <FormField label="Description">
            <Input
              value={formData.description}
              onChange={({ detail }) => setFormData({ ...formData, description: detail.value })}
              placeholder="Brief description of this rule"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        visible={!!deleteConfirm}
        onDismiss={() => setDeleteConfirm(null)}
        header="Delete Scoping Rule"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setDeleteConfirm(null)}>Cancel</Button>
              <Button variant="primary" onClick={handleDelete} loading={actionLoading}>
                Delete
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Box>
          Are you sure you want to delete this scoping rule?
          <Box margin={{ top: 's' }}>
            <strong>Type:</strong> {RULE_TYPES.find(t => t.value === deleteConfirm?.type)?.label}<br />
            <strong>Pattern:</strong> <code>{deleteConfirm?.pattern}</code><br />
            <strong>Team:</strong> {deleteConfirm ? getTeamName(deleteConfirm.teamId) : ''}
          </Box>
        </Box>
      </Modal>
    </SpaceBetween>
  );
}
