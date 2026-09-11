import { useState, useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import Table from '@cloudscape-design/components/table';
import Header from '@cloudscape-design/components/header';
import Box from '@cloudscape-design/components/box';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Badge from '@cloudscape-design/components/badge';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import TextFilter from '@cloudscape-design/components/text-filter';
import Select from '@cloudscape-design/components/select';
import Button from '@cloudscape-design/components/button';
import Modal from '@cloudscape-design/components/modal';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import DatePicker from '@cloudscape-design/components/date-picker';
import Textarea from '@cloudscape-design/components/textarea';
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar';
import Link from '@cloudscape-design/components/link';
import Popover from '@cloudscape-design/components/popover';
import { getLifecycleData, getActionPlans, createActionPlan, getScanners, DeprecationItem, ActionPlan, ScanCoverage } from '../api';
import {
  statusMeta, isConcern, getDeadline, formatDate, formatDaysLeft, urgencySort, serviceLabel, itemName, STATUS_META,
} from '../lifecycle';

const SCOPE_OPTIONS = [
  { label: 'Needs attention', value: 'concerns' },
  { label: 'Everything found', value: 'all' },
  ...Object.entries(STATUS_META).map(([value, m]) => ({ label: m.label, value })),
];

const PRIORITY_OPTIONS = [
  { label: 'Low', value: 'low' }, { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' }, { label: 'Critical', value: 'critical' },
];

const relative = (iso: string | null | undefined): string => {
  if (!iso) return 'never';
  const h = Math.round((Date.now() - new Date(iso).getTime()) / 3_600_000);
  return h < 1 ? 'less than an hour ago' : h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
};

export default function MyResources() {
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<DeprecationItem[]>([]);
  const [facts, setFacts] = useState<DeprecationItem[]>([]);
  const [plans, setPlans] = useState<ActionPlan[]>([]);
  const [coverage, setCoverage] = useState<ScanCoverage | null>(null);
  const [loading, setLoading] = useState(true);
  const [flashbarItems, setFlashbarItems] = useState<FlashbarProps.MessageDefinition[]>([]);
  const [filterText, setFilterText] = useState(params.get('q') || '');
  const [scope, setScope] = useState(params.get('status') || 'concerns');
  const [service, setService] = useState(params.get('service') || 'all');
  const [selected, setSelected] = useState<DeprecationItem[]>([]);
  const [showPlanModal, setShowPlanModal] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({ owner: '', priority: 'medium', target_date: '', notes: '' });

  useEffect(() => { load(); }, []);

  const flash = (type: FlashbarProps.Type, content: string) =>
    setFlashbarItems([{ type, content, dismissible: true, dismissLabel: 'Dismiss', onDismiss: () => setFlashbarItems([]), id: `${type}-${Date.now()}` }]);

  const load = async () => {
    try {
      setLoading(true);
      const [data, p, cov] = await Promise.all([getLifecycleData(), getActionPlans(), getScanners()]);
      setRows(data.inventory);
      setFacts(data.facts);
      setPlans(p);
      setCoverage(cov);
    } catch (err: any) {
      flash('error', `Failed to load resources: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  // plan owner per inventory row (item_id match)
  const planByItem = useMemo(() => {
    const m = new Map<string, ActionPlan>();
    for (const p of plans) m.set(`${p.service_name}|${p.item_id}`, p);
    return m;
  }, [plans]);

  const factById = useMemo(() => {
    const m = new Map<string, DeprecationItem>();
    for (const f of facts) m.set(`${f.service_name}|${f.item_id}`, f);
    return m;
  }, [facts]);

  const services = useMemo(() => [...new Set(rows.map((r) => r.service_name))].sort(), [rows]);

  const filtered = useMemo(() => {
    let out = [...rows];
    if (scope === 'concerns') out = out.filter((r) => isConcern(r.status));
    else if (scope !== 'all') out = out.filter((r) => r.status === scope);
    if (service !== 'all') out = out.filter((r) => r.service_name === service);
    if (filterText) {
      const q = filterText.toLowerCase();
      out = out.filter((r) => `${r.service_name} ${serviceLabel(r.service_name)} ${itemName(r)} ${JSON.stringify(r.service_specific)} ${r.region || ''}`.toLowerCase().includes(q));
    }
    return out.sort(urgencySort);
  }, [rows, scope, service, filterText]);

  const updateParams = (next: Record<string, string>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) { if (v && v !== 'all' && v !== 'concerns') p.set(k, v); else p.delete(k); }
    setParams(p, { replace: true });
  };

  const handleAddToPlan = async () => {
    if (!form.owner.trim()) { flash('error', 'Owner is required'); return; }
    setSubmitting(true);
    let ok = 0, ko = 0;
    for (const r of selected) {
      try {
        const res = await createActionPlan({
          service_name: r.service_name, item_id: r.item_id,
          item_name: `${serviceLabel(r.service_name)} ${itemName(r)} (${r.service_specific?.total_affected ?? 0} resources)`,
          owner: form.owner, priority: form.priority, target_date: form.target_date, notes: form.notes,
        });
        res.success ? ok++ : ko++;
      } catch { ko++; }
    }
    setSubmitting(false);
    setShowPlanModal(false);
    setForm({ owner: '', priority: 'medium', target_date: '', notes: '' });
    setSelected([]);
    flash(ko ? 'warning' : 'success', ko ? `Created ${ok} plan(s), ${ko} failed` : `Created ${ok} plan${ok === 1 ? '' : 's'}`);
    load();
  };

  return (
    <SpaceBetween size="l">
      <Flashbar items={flashbarItems} stackItems />

      <Table
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        trackBy="item_id"
        items={filtered}
        loading={loading}
        loadingText="Loading your resources..."
        variant="full-page"
        stickyHeader
        header={
          <Header
            variant="h1"
            counter={`(${filtered.length})`}
            description={`What the account scan found, matched against the catalog. Last scan ${relative(coverage?.last_scan.last_verified)}${coverage?.last_scan.regions.length ? ` in ${coverage.last_scan.regions.join(', ')}` : ''}.`}
            actions={
              <Button variant="primary" disabled={selected.length === 0} onClick={() => setShowPlanModal(true)}>
                Create plan{selected.length ? ` (${selected.length})` : ''}
              </Button>
            }
          >
            My resources
          </Header>
        }
        filter={
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            <TextFilter filteringText={filterText} filteringPlaceholder="Search versions, resources, regions..."
              filteringAriaLabel="Filter resources"
              onChange={({ detail }) => { setFilterText(detail.filteringText); updateParams({ q: detail.filteringText }); }} />
            <Select selectedOption={SCOPE_OPTIONS.find((o) => o.value === scope) || SCOPE_OPTIONS[0]}
              onChange={({ detail }) => { setScope(detail.selectedOption.value!); updateParams({ status: detail.selectedOption.value! }); }}
              options={SCOPE_OPTIONS} selectedAriaLabel="Selected" />
            <Select selectedOption={{ label: service === 'all' ? 'All services' : serviceLabel(service), value: service }}
              onChange={({ detail }) => { setService(detail.selectedOption.value!); updateParams({ service: detail.selectedOption.value! }); }}
              options={[{ label: 'All services', value: 'all' }, ...services.map((s) => ({ label: serviceLabel(s), value: s }))]}
              selectedAriaLabel="Selected" />
          </div>
        }
        empty={
          <Box textAlign="center" padding="l" color="text-body-secondary">
            <Box variant="strong">
              {rows.length === 0 ? 'No resources scanned yet' : scope === 'concerns' ? 'Nothing needs attention' : 'No resources match these filters'}
            </Box>
            <Box variant="p">
              {rows.length === 0
                ? 'Click Refresh on the dashboard to scan this account.'
                : `Last scan ${relative(coverage?.last_scan.last_verified)}. ${scope === 'concerns' ? 'Switch the scope to "Everything found" to see supported versions too.' : ''}`}
            </Box>
          </Box>
        }
        columnDefinitions={[
          { id: 'service', header: 'Service', cell: (r) => <Badge color="blue">{serviceLabel(r.service_name)}</Badge>, sortingField: 'service_name' },
          {
            id: 'version', header: 'Version', cell: (r) => (
              <SpaceBetween size="xxxs">
                <Box variant="strong">{itemName(r)}</Box>
                <Box variant="small" color="text-body-secondary">{r.service_specific?.identifier}</Box>
              </SpaceBetween>
            ),
          },
          {
            id: 'status', header: 'Status', cell: (r) => {
              const m = statusMeta(r.status);
              const fact = r.service_specific?.matched_lifecycle_item ? factById.get(`${r.service_name}|${r.service_specific.matched_lifecycle_item}`) : undefined;
              return (
                <SpaceBetween size="xxxs">
                  <StatusIndicator type={m.indicator}>{m.label}</StatusIndicator>
                  {fact ? (
                    <Popover dismissButton={false} position="right" size="medium" triggerType="text"
                      content={
                        <SpaceBetween size="xxs">
                          <Box variant="strong">Matched catalog entry</Box>
                          <Box variant="small">{itemName(fact)}</Box>
                          {Object.entries(fact.service_specific).filter(([k, v]) => k.endsWith('_date') && v && v !== 'N/A').map(([k, v]) => (
                            <Box key={k} variant="small">{k.replace(/_/g, ' ')}: {String(v)}</Box>
                          ))}
                          {fact.source_url && <Link href={fact.source_url} external fontSize="body-s">AWS documentation</Link>}
                        </SpaceBetween>
                      }>
                      <Box variant="small" color="text-status-info">why?</Box>
                    </Popover>
                  ) : r.status === 'unknown' ? <Box variant="small" color="text-body-secondary">no catalog entry</Box> : null}
                </SpaceBetween>
              );
            },
          },
          {
            id: 'deadline', header: 'Deadline', cell: (r) => {
              const d = getDeadline(r);
              return d ? (
                <SpaceBetween size="xxxs">
                  <Box>{formatDate(d.date)}</Box>
                  <Box variant="small" color={d.daysLeft < 0 ? 'text-status-error' : d.daysLeft <= 90 ? 'text-status-warning' : 'text-body-secondary'}>{formatDaysLeft(d)}</Box>
                </SpaceBetween>
              ) : <Box color="text-body-secondary">-</Box>;
            },
          },
          {
            id: 'resources', header: 'Resources', cell: (r) => (
              <SpaceBetween size="xxxs">
                <Badge color="grey">{r.service_specific?.total_affected ?? 0}</Badge>
                <Box variant="small" color="text-body-secondary">{r.service_specific?.affected_resources}</Box>
              </SpaceBetween>
            ),
          },
          { id: 'region', header: 'Region', cell: (r) => r.region || '-', sortingField: 'region' },
          {
            id: 'plan', header: 'Plan', cell: (r) => {
              const p = planByItem.get(`${r.service_name}|${r.item_id}`);
              return p
                ? <StatusIndicator type={p.plan_status === 'completed' ? 'success' : p.plan_status === 'blocked' ? 'error' : p.plan_status === 'in_progress' ? 'in-progress' : 'pending'}>{p.owner}</StatusIndicator>
                : isConcern(r.status) ? <Box variant="small" color="text-body-secondary">unassigned</Box> : null;
            },
          },
          { id: 'verified', header: 'Seen', cell: (r) => <Box variant="small">{formatDate(r.last_verified)}</Box> },
        ]}
      />

      <Modal
        visible={showPlanModal}
        onDismiss={() => setShowPlanModal(false)}
        header={`Create plan for ${selected.length} version${selected.length === 1 ? '' : 's'}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowPlanModal(false)}>Cancel</Button>
              <Button variant="primary" loading={submitting} onClick={handleAddToPlan}>Create</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box variant="small" color="text-body-secondary">
            {selected.map((r) => `${serviceLabel(r.service_name)} ${itemName(r)}`).join(' · ')}
          </Box>
          <FormField label="Owner (email/alias)">
            <Input value={form.owner} onChange={({ detail }) => setForm({ ...form, owner: detail.value })} placeholder="e.g. jane@example.com" />
          </FormField>
          <FormField label="Priority">
            <Select selectedOption={PRIORITY_OPTIONS.find((o) => o.value === form.priority) || null}
              onChange={({ detail }) => setForm({ ...form, priority: detail.selectedOption.value || 'medium' })} options={PRIORITY_OPTIONS} />
          </FormField>
          <FormField label="Target date">
            <DatePicker value={form.target_date} onChange={({ detail }) => setForm({ ...form, target_date: detail.value })} placeholder="YYYY/MM/DD" />
          </FormField>
          <FormField label="Notes">
            <Textarea value={form.notes} onChange={({ detail }) => setForm({ ...form, notes: detail.value })} placeholder="Migration plan, blockers, ..." />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
}
