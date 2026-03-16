import { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import Button from '@cloudscape-design/components/button';
import Box from '@cloudscape-design/components/box';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Badge from '@cloudscape-design/components/badge';
import Modal from '@cloudscape-design/components/modal';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Select from '@cloudscape-design/components/select';
import Textarea from '@cloudscape-design/components/textarea';
import DatePicker from '@cloudscape-design/components/date-picker';
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar';
import { 
  getActionPlans, 
  createActionPlan, 
  updateActionPlan, 
  deleteActionPlan,
  getDeprecations,
  ActionPlan 
} from '../api';

const STATUS_OPTIONS = [
  { label: 'Not Started', value: 'not_started' },
  { label: 'In Progress', value: 'in_progress' },
  { label: 'Completed', value: 'completed' },
  { label: 'Blocked', value: 'blocked' },
];

const PRIORITY_OPTIONS = [
  { label: 'Low', value: 'low' },
  { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' },
  { label: 'Critical', value: 'critical' },
];

export default function PlanOfAction() {
  const [plans, setPlans] = useState<ActionPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [flashbarItems, setFlashbarItems] = useState<FlashbarProps.MessageDefinition[]>([]);
  const [selectedPlan, setSelectedPlan] = useState<ActionPlan | null>(null);
  
  // Modal states
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  
  // Form state
  const [formData, setFormData] = useState({
    service_name: '',
    item_id: '',
    item_name: '',
    owner: '',
    plan_status: 'not_started',
    priority: 'medium',
    target_date: '',
    notes: '',
  });
  
  // Deprecations for dropdown
  const [deprecations, setDeprecations] = useState<any[]>([]);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [plansData, deprecationsData] = await Promise.all([
        getActionPlans(),
        getDeprecations({ status: 'deprecated' })
      ]);
      setPlans(plansData);
      setDeprecations(deprecationsData);
    } catch (err: any) {
      showError(`Failed to load data: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };


  const showError = (message: string) => {
    setFlashbarItems([{
      type: 'error',
      dismissible: true,
      dismissLabel: 'Dismiss',
      onDismiss: () => setFlashbarItems([]),
      content: message,
      id: `error-${Date.now()}`
    }]);
  };

  const showSuccess = (message: string) => {
    setFlashbarItems([{
      type: 'success',
      dismissible: true,
      dismissLabel: 'Dismiss',
      onDismiss: () => setFlashbarItems([]),
      content: message,
      id: `success-${Date.now()}`
    }]);
  };

  const handleCreate = async () => {
    try {
      const result = await createActionPlan(formData);
      if (result.success) {
        showSuccess('Action plan created successfully');
        setShowCreateModal(false);
        resetForm();
        loadData();
      } else {
        showError(result.error || 'Failed to create action plan');
      }
    } catch (err: any) {
      showError(err.message);
    }
  };

  const handleUpdate = async () => {
    if (!selectedPlan) return;
    try {
      const result = await updateActionPlan(selectedPlan.plan_id, {
        owner: formData.owner,
        plan_status: formData.plan_status as any,
        priority: formData.priority as any,
        target_date: formData.target_date,
        notes: formData.notes,
      });
      if (result.success) {
        showSuccess('Action plan updated successfully');
        setShowEditModal(false);
        resetForm();
        loadData();
      } else {
        showError(result.error || 'Failed to update action plan');
      }
    } catch (err: any) {
      showError(err.message);
    }
  };

  const handleDelete = async () => {
    if (!selectedPlan) return;
    try {
      const result = await deleteActionPlan(selectedPlan.plan_id);
      if (result.success) {
        showSuccess('Action plan deleted');
        setShowDeleteModal(false);
        setSelectedPlan(null);
        loadData();
      } else {
        showError(result.error || 'Failed to delete action plan');
      }
    } catch (err: any) {
      showError(err.message);
    }
  };

  const resetForm = () => {
    setFormData({
      service_name: '',
      item_id: '',
      item_name: '',
      owner: '',
      plan_status: 'not_started',
      priority: 'medium',
      target_date: '',
      notes: '',
    });
  };

  const openEditModal = (plan: ActionPlan) => {
    setSelectedPlan(plan);
    setFormData({
      service_name: plan.service_name,
      item_id: plan.item_id,
      item_name: plan.item_name,
      owner: plan.owner,
      plan_status: plan.plan_status,
      priority: plan.priority,
      target_date: plan.target_date,
      notes: plan.notes,
    });
    setShowEditModal(true);
  };

  const getStatusIndicator = (status: string) => {
    switch (status) {
      case 'completed': return <StatusIndicator type="success">Completed</StatusIndicator>;
      case 'in_progress': return <StatusIndicator type="in-progress">In Progress</StatusIndicator>;
      case 'blocked': return <StatusIndicator type="error">Blocked</StatusIndicator>;
      default: return <StatusIndicator type="pending">Not Started</StatusIndicator>;
    }
  };

  const getPriorityBadge = (priority: string) => {
    switch (priority) {
      case 'critical': return <Badge color="red">Critical</Badge>;
      case 'high': return <Badge color="red">High</Badge>;
      case 'medium': return <Badge color="blue">Medium</Badge>;
      default: return <Badge color="grey">Low</Badge>;
    }
  };

  // Build deprecation options for select
  const deprecationOptions = deprecations.map(d => ({
    label: `${d.service_name} - ${d.service_specific?.name || d.item_id}`,
    value: `${d.service_name}|${d.item_id}|${d.service_specific?.name || d.item_id}`,
  }));


  return (
    <SpaceBetween size="l">
      <Flashbar items={flashbarItems} stackItems />

      <Container
        header={
          <Header
            variant="h1"
            actions={
              <Button variant="primary" onClick={() => setShowCreateModal(true)}>
                Create Action Plan
              </Button>
            }
            description="Track and manage remediation plans for deprecated resources"
          >
            Plan of Action ({plans.length})
          </Header>
        }
      >
        <Table
          loading={loading}
          loadingText="Loading action plans..."
          items={plans}
          empty={
            <Box textAlign="center" color="text-body-secondary" padding="l">
              <Box variant="strong">No action plans</Box>
              <Box variant="p">Create an action plan to track deprecation remediation</Box>
            </Box>
          }
          columnDefinitions={[
            {
              id: 'service',
              header: 'Service',
              cell: item => <Box variant="strong">{item.service_name}</Box>,
              width: 120,
            },
            {
              id: 'item',
              header: 'Deprecation Item',
              cell: item => item.item_name || item.item_id,
              width: 180,
            },
            {
              id: 'owner',
              header: 'Owner',
              cell: item => item.owner,
              width: 150,
            },
            {
              id: 'status',
              header: 'Status',
              cell: item => getStatusIndicator(item.plan_status),
              width: 130,
            },
            {
              id: 'priority',
              header: 'Priority',
              cell: item => getPriorityBadge(item.priority),
              width: 100,
            },
            {
              id: 'target_date',
              header: 'Target Date',
              cell: item => item.target_date || '-',
              width: 120,
            },
            {
              id: 'notes',
              header: 'Notes',
              cell: item => item.notes ? (item.notes.length > 50 ? item.notes.substring(0, 50) + '...' : item.notes) : '-',
              width: 200,
            },
            {
              id: 'actions',
              header: 'Actions',
              cell: item => (
                <SpaceBetween direction="horizontal" size="xs">
                  <Button variant="link" onClick={() => openEditModal(item)}>Edit</Button>
                  <Button variant="link" onClick={() => { setSelectedPlan(item); setShowDeleteModal(true); }}>Delete</Button>
                </SpaceBetween>
              ),
              width: 150,
            },
          ]}
        />
      </Container>

      {/* Create Modal */}
      <Modal
        visible={showCreateModal}
        onDismiss={() => { setShowCreateModal(false); resetForm(); }}
        header="Create Action Plan"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => { setShowCreateModal(false); resetForm(); }}>Cancel</Button>
              <Button variant="primary" onClick={handleCreate}>Create</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Deprecation Item">
            <Select
              selectedOption={deprecationOptions.find(o => o.value === `${formData.service_name}|${formData.item_id}|${formData.item_name}`) || null}
              onChange={({ detail }) => {
                const [service, itemId, itemName] = (detail.selectedOption.value || '').split('|');
                setFormData({ ...formData, service_name: service, item_id: itemId, item_name: itemName });
              }}
              options={deprecationOptions}
              placeholder="Select a deprecated item"
            />
          </FormField>
          <FormField label="Owner (email/alias)">
            <Input
              value={formData.owner}
              onChange={({ detail }) => setFormData({ ...formData, owner: detail.value })}
              placeholder="e.g., john@example.com"
            />
          </FormField>
          <FormField label="Priority">
            <Select
              selectedOption={PRIORITY_OPTIONS.find(o => o.value === formData.priority) || null}
              onChange={({ detail }) => setFormData({ ...formData, priority: detail.selectedOption.value || 'medium' })}
              options={PRIORITY_OPTIONS}
            />
          </FormField>
          <FormField label="Target Date">
            <DatePicker
              value={formData.target_date}
              onChange={({ detail }) => setFormData({ ...formData, target_date: detail.value })}
              placeholder="YYYY/MM/DD"
            />
          </FormField>
          <FormField label="Notes">
            <Textarea
              value={formData.notes}
              onChange={({ detail }) => setFormData({ ...formData, notes: detail.value })}
              placeholder="Migration plan details, blockers, etc."
            />
          </FormField>
        </SpaceBetween>
      </Modal>


      {/* Edit Modal */}
      <Modal
        visible={showEditModal}
        onDismiss={() => { setShowEditModal(false); resetForm(); setSelectedPlan(null); }}
        header="Edit Action Plan"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => { setShowEditModal(false); resetForm(); setSelectedPlan(null); }}>Cancel</Button>
              <Button variant="primary" onClick={handleUpdate}>Save</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Deprecation Item">
            <Box>{formData.service_name} - {formData.item_name}</Box>
          </FormField>
          <FormField label="Owner (email/alias)">
            <Input
              value={formData.owner}
              onChange={({ detail }) => setFormData({ ...formData, owner: detail.value })}
            />
          </FormField>
          <FormField label="Status">
            <Select
              selectedOption={STATUS_OPTIONS.find(o => o.value === formData.plan_status) || null}
              onChange={({ detail }) => setFormData({ ...formData, plan_status: detail.selectedOption.value || 'not_started' })}
              options={STATUS_OPTIONS}
            />
          </FormField>
          <FormField label="Priority">
            <Select
              selectedOption={PRIORITY_OPTIONS.find(o => o.value === formData.priority) || null}
              onChange={({ detail }) => setFormData({ ...formData, priority: detail.selectedOption.value || 'medium' })}
              options={PRIORITY_OPTIONS}
            />
          </FormField>
          <FormField label="Target Date">
            <DatePicker
              value={formData.target_date}
              onChange={({ detail }) => setFormData({ ...formData, target_date: detail.value })}
              placeholder="YYYY/MM/DD"
            />
          </FormField>
          <FormField label="Notes">
            <Textarea
              value={formData.notes}
              onChange={({ detail }) => setFormData({ ...formData, notes: detail.value })}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        visible={showDeleteModal}
        onDismiss={() => { setShowDeleteModal(false); setSelectedPlan(null); }}
        header="Delete Action Plan"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => { setShowDeleteModal(false); setSelectedPlan(null); }}>Cancel</Button>
              <Button variant="primary" onClick={handleDelete}>Delete</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Box>
          Are you sure you want to delete the action plan for{' '}
          <strong>{selectedPlan?.service_name} - {selectedPlan?.item_name}</strong>?
        </Box>
      </Modal>
    </SpaceBetween>
  );
}
