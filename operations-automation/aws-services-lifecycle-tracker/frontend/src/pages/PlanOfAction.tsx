import { useState, useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import TextFilter from '@cloudscape-design/components/text-filter';
import Pagination from '@cloudscape-design/components/pagination';
import CollectionPreferences, { CollectionPreferencesProps } from '@cloudscape-design/components/collection-preferences';
import { useCollection } from '@cloudscape-design/collection-hooks';
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
  getLifecycleData,
  ActionPlan,
  DeprecationItem
} from '../api';
import { isConcern, urgencySort, serviceLabel, itemName, statusMeta, resourceCount, resourceWord } from '../lifecycle';

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
const PRIORITY_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };
const STATUS_RANK: Record<string, number> = { blocked: 0, in_progress: 1, not_started: 2, completed: 3 };

// Filter dropdowns: "all" first, then the values
const withAll = (label: string, options: { label: string; value: string }[]) => [{ label, value: 'all' }, ...options];
const STATUS_FILTER = withAll('Any status', STATUS_OPTIONS);
const PRIORITY_FILTER = withAll('Any priority', PRIORITY_OPTIONS);

// Table preferences (page size, visible columns); kept per browser
const PREFS_KEY = 'lifecycle-plans-preferences';
const DEFAULT_PREFS: CollectionPreferencesProps.Preferences = {
  pageSize: 50,
  contentDisplay: ['service', 'item', 'owner', 'status', 'priority', 'target_date', 'notes'].map((id) => ({ id, visible: true })),
};
const loadPrefs = (): CollectionPreferencesProps.Preferences => {
  try { return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') }; } catch { return DEFAULT_PREFS; }
};

export default function PlanOfAction() {
  const [params, setParams] = useSearchParams();
  const [plans, setPlans] = useState<ActionPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [flashbarItems, setFlashbarItems] = useState<FlashbarProps.MessageDefinition[]>([]);
  const [selectedPlan, setSelectedPlan] = useState<ActionPlan | null>(null);
  const [preferences, setPreferences] = useState(loadPrefs);
  // Filters, mirrored in the URL like the other pages
  const filterText = params.get('q') || '';
  const status = params.get('status') || 'all';
  const priority = params.get('priority') || 'all';
  const owner = params.get('owner') || 'all';
  const updateParams = (next: Record<string, string>) => setParams((prev) => {
    const p = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(next)) { if (v && v !== 'all') p.set(k, v); else p.delete(k); }
    return p;
  }, { replace: true });
  
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
  
  // My resources needing attention, for the create-plan picker (issue #141)
  const [candidates, setCandidates] = useState<DeprecationItem[]>([]);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [plansData, data] = await Promise.all([getActionPlans(), getLifecycleData()]);
      setPlans(plansData);
      // Plans are for things you own: offer inventory rows that need attention first,
      // then the rest of the inventory (a supported version can still warrant a plan).
      setCandidates([...data.inventory].sort(urgencySort));
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

  const owners = useMemo(() => [...new Set(plans.map((p) => p.owner).filter(Boolean))].sort(), [plans]);
  const scoped = useMemo(() => plans.filter((p) =>
    (status === 'all' || p.plan_status === status)
    && (priority === 'all' || p.priority === priority)
    && (owner === 'all' || p.owner === owner)), [plans, status, priority, owner]);

  const { items, filteredItemsCount, collectionProps, filterProps, paginationProps } = useCollection(scoped, {
    filtering: {
      defaultFilteringText: filterText,
      filteringFunction: (p, text) => `${p.service_name} ${serviceLabel(p.service_name)} ${p.item_name} ${p.item_id} ${p.owner} ${p.notes || ''}`.toLowerCase().includes(text.toLowerCase()),
    },
    // default order: blocked and in progress first, then priority, then target date
    sorting: { defaultState: { sortingColumn: { sortingComparator: (a: ActionPlan, b: ActionPlan) =>
      (STATUS_RANK[a.plan_status] ?? 9) - (STATUS_RANK[b.plan_status] ?? 9)
      || (PRIORITY_RANK[a.priority] ?? 9) - (PRIORITY_RANK[b.priority] ?? 9)
      || (a.target_date || '9999').localeCompare(b.target_date || '9999') } } } },
    pagination: { pageSize: preferences.pageSize },
    selection: { trackBy: 'plan_id' },
  });
  const shown = filteredItemsCount ?? scoped.length;
  const selectedRow = collectionProps.selectedItems?.[0];

  // Picker options: my resources, urgent first, with status and resource count
  const deprecationOptions = candidates.map(d => ({
    label: `${serviceLabel(d.service_name)} - ${itemName(d)}`,
    description: `${statusMeta(d.status).label} - ${resourceCount(d)} ${resourceWord(resourceCount(d))}${d.region ? ` in ${d.region}` : ''}`,
    value: `${d.service_name}|${d.item_id}|${serviceLabel(d.service_name)} ${itemName(d)}`,
    tags: isConcern(d.status) ? ['needs attention'] : undefined,
  }));


  return (
    <SpaceBetween size="l">
      <Flashbar items={flashbarItems} stackItems />

      <Table
        {...collectionProps}
        selectionType="single"
        items={items}
        columnDisplay={preferences.contentDisplay}
        loading={loading}
        loadingText="Loading action plans..."
        variant="full-page"
        stickyHeader
        wrapLines
        header={
          <Header
            variant="h1"
            counter={shown === plans.length ? `(${plans.length})` : `(${shown} of ${plans.length})`}
            description="Who is upgrading what, by when. Create plans here or from My resources; select a plan to edit or delete it."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button disabled={!selectedRow} onClick={() => selectedRow && openEditModal(selectedRow)}>Edit</Button>
                <Button disabled={!selectedRow} onClick={() => { if (selectedRow) { setSelectedPlan(selectedRow); setShowDeleteModal(true); } }}>Delete</Button>
                <Button variant="primary" onClick={() => setShowCreateModal(true)}>Create plan</Button>
              </SpaceBetween>
            }
          >
            Plan of Action
          </Header>
        }
        filter={
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            <TextFilter {...filterProps} filteringPlaceholder="Search versions, owners, notes..." filteringAriaLabel="Filter plans"
              countText={`${shown} match${shown === 1 ? '' : 'es'}`}
              onChange={(e) => { filterProps.onChange(e); updateParams({ q: e.detail.filteringText }); }} />
            <Select selectedOption={STATUS_FILTER.find((o) => o.value === status) || STATUS_FILTER[0]}
              onChange={({ detail }) => updateParams({ status: detail.selectedOption.value! })} options={STATUS_FILTER} selectedAriaLabel="Selected" />
            <Select selectedOption={PRIORITY_FILTER.find((o) => o.value === priority) || PRIORITY_FILTER[0]}
              onChange={({ detail }) => updateParams({ priority: detail.selectedOption.value! })} options={PRIORITY_FILTER} selectedAriaLabel="Selected" />
            <Select selectedOption={{ label: owner === 'all' ? 'Any owner' : owner, value: owner }}
              onChange={({ detail }) => updateParams({ owner: detail.selectedOption.value! })}
              options={withAll('Any owner', owners.map((o) => ({ label: o, value: o })))} selectedAriaLabel="Selected" />
          </div>
        }
        pagination={<Pagination {...paginationProps} />}
        preferences={
          <CollectionPreferences
            title="Preferences" confirmLabel="Confirm" cancelLabel="Cancel"
            preferences={preferences}
            onConfirm={({ detail }) => { setPreferences(detail); localStorage.setItem(PREFS_KEY, JSON.stringify(detail)); }}
            pageSizePreference={{ title: 'Page size', options: [25, 50, 100].map((n) => ({ value: n, label: `${n} plans` })) }}
            contentDisplayPreference={{
              title: 'Columns',
              options: [
                { id: 'service', label: 'Service', alwaysVisible: true }, { id: 'item', label: 'Version', alwaysVisible: true },
                { id: 'owner', label: 'Owner' }, { id: 'status', label: 'Status' }, { id: 'priority', label: 'Priority' },
                { id: 'target_date', label: 'Target date' }, { id: 'notes', label: 'Notes' },
              ],
            }}
          />
        }
        empty={
          <Box textAlign="center" color="text-body-secondary" padding="l">
            <SpaceBetween size="xs">
              <Box variant="strong">{plans.length === 0 ? 'No plans yet' : 'No plans match these filters'}</Box>
              <Box variant="p">{plans.length === 0 ? 'A plan says who upgrades a version and by when.' : 'Try another status, priority or owner.'}</Box>
              {plans.length === 0
                ? <Button onClick={() => setShowCreateModal(true)}>Create plan</Button>
                : <Button onClick={() => { filterProps.onChange({ detail: { filteringText: '' } } as any); updateParams({ q: '', status: '', priority: '', owner: '' }); }}>Clear filters</Button>}
            </SpaceBetween>
          </Box>
        }
        columnDefinitions={[
          { id: 'service', header: 'Service', cell: (item) => <Badge color="blue">{serviceLabel(item.service_name)}</Badge>, sortingField: 'service_name' },
          { id: 'item', header: 'Version', cell: (item) => item.item_name || item.item_id, sortingField: 'item_name' },
          { id: 'owner', header: 'Owner', cell: (item) => item.owner, sortingField: 'owner' },
          { id: 'status', header: 'Status', cell: (item) => getStatusIndicator(item.plan_status), sortingComparator: (a, b) => (STATUS_RANK[a.plan_status] ?? 9) - (STATUS_RANK[b.plan_status] ?? 9) },
          { id: 'priority', header: 'Priority', cell: (item) => getPriorityBadge(item.priority), sortingComparator: (a, b) => (PRIORITY_RANK[a.priority] ?? 9) - (PRIORITY_RANK[b.priority] ?? 9) },
          { id: 'target_date', header: 'Target date', cell: (item) => item.target_date || <Box color="text-body-secondary">-</Box>, sortingField: 'target_date' },
          { id: 'notes', header: 'Notes', cell: (item) => item.notes || <Box color="text-body-secondary">-</Box> },
        ]}
      />

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
          <FormField label="Resource (version running in this account)">
            <Select
              selectedOption={deprecationOptions.find(o => o.value === `${formData.service_name}|${formData.item_id}|${formData.item_name}`) || null}
              onChange={({ detail }) => {
                const [service, itemId, itemName] = (detail.selectedOption.value || '').split('|');
                setFormData({ ...formData, service_name: service, item_id: itemId, item_name: itemName });
              }}
              options={deprecationOptions}
              placeholder="Select one of your resources"
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
          <FormField label="Resource (version running in this account)">
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
