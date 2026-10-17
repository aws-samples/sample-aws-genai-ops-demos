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
  Textarea,
  Spinner,
  Alert,
  TokenGroup,
} from '@cloudscape-design/components';
import { UserInfo } from '../App';
import { Team } from '../types';
import { getTeams, createTeam, updateTeam, deleteTeam } from '../services/api';

interface TeamsProps {
  userInfo: UserInfo | null;
}

interface TeamFormData {
  name: string;
  description: string;
  members: string[];
  slackChannel: string;
  costCenter: string;
}

const initialFormData: TeamFormData = {
  name: '',
  description: '',
  members: [],
  slackChannel: '',
  costCenter: '',
};

export default function Teams({ userInfo }: TeamsProps) {
  const [teams, setTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  
  // Modal state
  const [showModal, setShowModal] = useState(false);
  const [editingTeam, setEditingTeam] = useState<Team | null>(null);
  const [formData, setFormData] = useState<TeamFormData>(initialFormData);
  const [newMember, setNewMember] = useState('');
  
  // Delete confirmation
  const [deleteConfirm, setDeleteConfirm] = useState<Team | null>(null);
  
  // Result notification
  const [actionResult, setActionResult] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const isAdmin = userInfo?.groups.includes('finops-admin');

  useEffect(() => {
    loadTeams();
  }, []);

  async function loadTeams() {
    setLoading(true);
    try {
      const data = await getTeams();
      setTeams(data.teams);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load teams');
    } finally {
      setLoading(false);
    }
  }

  const openCreateModal = () => {
    setFormData(initialFormData);
    setEditingTeam(null);
    setShowModal(true);
  };

  const openEditModal = (team: Team) => {
    setFormData({
      name: team.name,
      description: team.description || '',
      members: team.members,
      slackChannel: team.slackChannel || '',
      costCenter: team.costCenter || '',
    });
    setEditingTeam(team);
    setShowModal(true);
  };

  const handleAddMember = () => {
    if (newMember && !formData.members.includes(newMember)) {
      setFormData({
        ...formData,
        members: [...formData.members, newMember],
      });
      setNewMember('');
    }
  };

  const handleRemoveMember = (member: string) => {
    setFormData({
      ...formData,
      members: formData.members.filter(m => m !== member),
    });
  };

  const handleSave = async () => {
    setActionLoading(true);
    try {
      if (editingTeam) {
        await updateTeam(editingTeam.teamId, {
          name: formData.name,
          description: formData.description || undefined,
          members: formData.members,
          slackChannel: formData.slackChannel || undefined,
          costCenter: formData.costCenter || undefined,
        });
        setActionResult({ type: 'success', message: 'Team updated successfully' });
      } else {
        await createTeam({
          name: formData.name,
          description: formData.description || undefined,
          members: formData.members,
          slackChannel: formData.slackChannel || undefined,
          costCenter: formData.costCenter || undefined,
        });
        setActionResult({ type: 'success', message: 'Team created successfully' });
      }
      setShowModal(false);
      loadTeams();
    } catch (err) {
      setActionResult({
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to save team',
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteConfirm) return;
    setActionLoading(true);
    try {
      await deleteTeam(deleteConfirm.teamId);
      setActionResult({ type: 'success', message: 'Team deleted successfully' });
      setDeleteConfirm(null);
      loadTeams();
    } catch (err) {
      setActionResult({
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to delete team',
      });
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <Box textAlign="center" padding="xxl">
        <Spinner size="large" />
        <Box variant="p" margin={{ top: 's' }}>Loading teams...</Box>
      </Box>
    );
  }

  if (error) {
    return (
      <Alert type="error" header="Error loading teams">
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
        description="Manage teams for findings ownership assignment"
        counter={`(${teams.length})`}
        actions={
          isAdmin && (
            <Button variant="primary" onClick={openCreateModal}>
              Create Team
            </Button>
          )
        }
      >
        Teams
      </Header>

      <Table
        columnDefinitions={[
          {
            id: 'name',
            header: 'Team Name',
            cell: item => <Box fontWeight="bold">{item.name}</Box>,
            sortingField: 'name',
          },
          {
            id: 'description',
            header: 'Description',
            cell: item => item.description || '-',
          },
          {
            id: 'members',
            header: 'Members',
            cell: item => item.members.length > 0 
              ? <TokenGroup items={item.members.map(m => ({ label: m }))} limit={3} readOnly />
              : '-',
          },
          {
            id: 'slackChannel',
            header: 'Slack Channel',
            cell: item => item.slackChannel || '-',
            width: 150,
          },
          {
            id: 'costCenter',
            header: 'Cost Center',
            cell: item => item.costCenter || '-',
            width: 120,
          },
          {
            id: 'actions',
            header: 'Actions',
            cell: item => (
              isAdmin && (
                <SpaceBetween direction="horizontal" size="xs">
                  <Button variant="link" onClick={() => openEditModal(item)}>Edit</Button>
                  <Button variant="link" onClick={() => setDeleteConfirm(item)}>Delete</Button>
                </SpaceBetween>
              )
            ),
            width: 150,
          },
        ]}
        items={teams}
        loading={loading}
        loadingText="Loading teams..."
        empty={
          <Box textAlign="center" padding="l">
            <Box variant="h3">No teams</Box>
            <Box variant="p">Create your first team to assign findings ownership.</Box>
            {isAdmin && (
              <Button onClick={openCreateModal}>Create Team</Button>
            )}
          </Box>
        }
        variant="full-page"
        stickyHeader
        sortingDisabled
      />

      {/* Create/Edit Modal */}
      <Modal
        visible={showModal}
        onDismiss={() => setShowModal(false)}
        header={editingTeam ? 'Edit Team' : 'Create Team'}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setShowModal(false)}>Cancel</Button>
              <Button
                variant="primary"
                onClick={handleSave}
                loading={actionLoading}
                disabled={!formData.name}
              >
                {editingTeam ? 'Save Changes' : 'Create Team'}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Team Name" constraintText="Required">
            <Input
              value={formData.name}
              onChange={({ detail }) => setFormData({ ...formData, name: detail.value })}
              placeholder="Enter team name"
            />
          </FormField>
          
          <FormField label="Description">
            <Textarea
              value={formData.description}
              onChange={({ detail }) => setFormData({ ...formData, description: detail.value })}
              placeholder="Brief description of the team"
            />
          </FormField>
          
          <FormField label="Members" description="Add email addresses of team members">
            <SpaceBetween size="s">
              <SpaceBetween direction="horizontal" size="xs">
                <Input
                  value={newMember}
                  onChange={({ detail }) => setNewMember(detail.value)}
                  placeholder="user@example.com"
                  onKeyDown={(e) => {
                    if (e.detail.key === 'Enter') {
                      e.preventDefault();
                      handleAddMember();
                    }
                  }}
                />
                <Button onClick={handleAddMember}>Add</Button>
              </SpaceBetween>
              {formData.members.length > 0 && (
                <TokenGroup
                  items={formData.members.map(m => ({ label: m, dismissLabel: `Remove ${m}` }))}
                  onDismiss={({ detail }) => handleRemoveMember(detail.itemIndex >= 0 ? formData.members[detail.itemIndex] : '')}
                />
              )}
            </SpaceBetween>
          </FormField>
          
          <FormField label="Slack Channel" description="For notifications (optional)">
            <Input
              value={formData.slackChannel}
              onChange={({ detail }) => setFormData({ ...formData, slackChannel: detail.value })}
              placeholder="#finops-team"
            />
          </FormField>
          
          <FormField label="Cost Center" description="For scoping rules (optional)">
            <Input
              value={formData.costCenter}
              onChange={({ detail }) => setFormData({ ...formData, costCenter: detail.value })}
              placeholder="CC-12345"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        visible={!!deleteConfirm}
        onDismiss={() => setDeleteConfirm(null)}
        header="Delete Team"
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
          Are you sure you want to delete team <strong>{deleteConfirm?.name}</strong>?
          This action cannot be undone.
        </Box>
      </Modal>
    </SpaceBetween>
  );
}
