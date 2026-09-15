import { useEffect, useState } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import Table from '@cloudscape-design/components/table';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import FormField from '@cloudscape-design/components/form-field';
import Select from '@cloudscape-design/components/select';
import Input from '@cloudscape-design/components/input';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import AttributeEditor from '@cloudscape-design/components/attribute-editor';
import TokenGroup from '@cloudscape-design/components/token-group';
import { ScanCoverage, ScanTargets, ScanTargetSource, EMPTY_TARGETS, saveScanTargets } from '../api';
import { accountLabel } from '../lifecycle';
import { SupportTierCell, SupportTierHeader } from '../components/SupportTierCell';

// Multi-account coverage (#144): which accounts the last scan resolved and
// reached, and the editor of the _scan_targets control row. Single-account
// deployments see one covered account (the hub) and can leave the editor alone.

const SOURCE_OPTIONS: Array<{ value: ScanTargetSource; label: string; description: string }> = [
  { value: 'hub', label: 'This account only', description: 'Default. The account the tracker runs in.' },
  { value: 'organization', label: 'Whole organization', description: 'Every active account, listed with AWS Organizations.' },
  { value: 'ou', label: 'Organizational units', description: 'Accounts under the given root / OU ids, recursively.' },
  { value: 'manual', label: 'Account list', description: 'The accounts entered below; no Organizations access needed.' },
];

const ACCOUNT_ID = /^\d{12}$/;
const OU_ID = /^(r-[a-z0-9]{4,32}|ou-[a-z0-9]{4,32}-[a-z0-9]{8,32})$/;
const REGION = /^[a-z]{2}(-[a-z]+)+-\d$/;
// Example shown in the Regions field: the region this deployment runs in (set at build time), never a literal
const DEPLOY_REGION: string = (import.meta as any).env?.VITE_REGION || 'a region code';

// A list of ids edited as tokens: type one (or several, comma separated), Enter adds them
function TokenListField({ label, description, tokens, onChange, placeholder, validate, invalidText }: {
  label: string; description: string; tokens: string[]; onChange: (next: string[]) => void;
  placeholder: string; validate: RegExp; invalidText: string;
}) {
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const add = () => {
    const parts = draft.split(/[\s,;]+/).map((t) => t.trim()).filter(Boolean);
    if (!parts.length) return;
    const bad = parts.find((t) => !validate.test(t));
    if (bad) { setError(`'${bad}' ${invalidText}`); return; }
    onChange([...new Set([...tokens, ...parts])]);
    setDraft(''); setError('');
  };
  return (
    <FormField label={label} description={description} errorText={error || undefined} stretch>
      <SpaceBetween size="xs">
        <SpaceBetween direction="horizontal" size="xs">
          <Input value={draft} placeholder={placeholder} onChange={({ detail }) => { setDraft(detail.value); setError(''); }}
            onKeyDown={({ detail }) => { if (detail.key === 'Enter') add(); }} />
          <Button onClick={add} disabled={!draft.trim()}>Add</Button>
        </SpaceBetween>
        {tokens.length > 0 && (
          <TokenGroup items={tokens.map((t) => ({ label: t, dismissLabel: `Remove ${t}` }))}
            onDismiss={({ detail }) => onChange(tokens.filter((_, i) => i !== detail.itemIndex))} />
        )}
      </SpaceBetween>
    </FormField>
  );
}

interface Props {
  coverage: ScanCoverage | null;
  onSaved: (message: string, ok: boolean) => void;
}

export default function ScanTargetsPanel({ coverage, onSaved }: Props) {
  const resolved = coverage?.accounts || null;
  const targets = coverage?.targets || EMPTY_TARGETS;
  const [source, setSource] = useState<ScanTargetSource>(targets.source);
  const [accounts, setAccounts] = useState<Array<{ id: string; name: string }>>([]);
  const [ouIds, setOuIds] = useState<string[]>([]);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [regions, setRegions] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  // (Re)fill the form when the stored targets arrive
  useEffect(() => {
    setSource(targets.source);
    setAccounts(targets.accounts.map((a) => ({ id: a.id, name: a.name })));
    setOuIds(targets.ou_ids);
    setExcluded(targets.exclude_accounts);
    setRegions(targets.regions);
  }, [coverage]);

  const accountError = (id: string) => (id && !ACCOUNT_ID.test(id.trim()) ? 'Must be a 12-digit account id' : undefined);
  const accountsValid = accounts.every((a) => !accountError(a.id));

  const scanned = new Set(resolved?.accounts_scanned || []);
  const failed = new Set(resolved?.accounts_failed || []);
  const rows = resolved?.accounts || [];

  const save = async () => {
    setSaving(true);
    const next: ScanTargets = {
      source,
      accounts: accounts.map((a) => ({ id: a.id.trim(), name: a.name.trim() })).filter((a) => a.id),
      ou_ids: ouIds,
      exclude_accounts: excluded,
      regions: regions.map((r) => r.toLowerCase()),
    };
    try {
      const res = await saveScanTargets(next);
      onSaved(res.success ? 'Scan targets saved. They apply to the next refresh.' : `Could not save scan targets: ${res.error}`, !!res.success);
    } catch (e: any) {
      onSaved(`Could not save scan targets: ${e.message}`, false);
    } finally {
      setSaving(false);
    }
  };

  const sourceLabel = SOURCE_OPTIONS.find((o) => o.value === (resolved?.source || 'hub'))?.label || resolved?.source;

  return (
    <Container
      header={
        <Header
          variant="h2"
          counter={rows.length ? `(${rows.length})` : undefined}
          description={resolved
            ? `${sourceLabel}${resolved.regions?.length ? ` · ${resolved.regions.join(', ')}` : ''}${resolved.scanned_at ? ` · last scan ${new Date(resolved.scanned_at).toLocaleString()}` : ''}`
            : 'No scan has run yet. Accounts appear here after the first refresh.'}
        >
          Accounts
        </Header>
      }
    >
      <SpaceBetween size="m">
        {resolved?.errors?.map((e, i) => (
          <StatusIndicator key={i} type="warning">{e}</StatusIndicator>
        ))}

        {rows.length > 0 && (
          <Table
            variant="embedded"
            items={rows}
            trackBy="id"
            columnDefinitions={[
              { id: 'account', header: 'Account', cell: (a) => (
                <SpaceBetween size="xxxs">
                  <Box variant="strong">{a.name || a.id}</Box>
                  {a.name && <Box variant="small" color="text-body-secondary">{a.id}</Box>}
                </SpaceBetween>
              ) },
              { id: 'ou', header: 'Organizational unit', cell: (a) => a.ou_path || <Box color="text-body-secondary">-</Box> },
              { id: 'support', header: <SupportTierHeader />, cell: (a) => <SupportTierCell status={coverage?.health?.by_account?.[a.id]} /> },
              { id: 'coverage', header: 'Last scan', cell: (a) => scanned.has(a.id)
                  ? <StatusIndicator type="success">covered</StatusIndicator>
                  : failed.has(a.id)
                    ? <StatusIndicator type="error">unreachable (spoke role missing or not trusting this hub)</StatusIndicator>
                    : <StatusIndicator type="pending">not scanned yet</StatusIndicator> },
            ]}
          />
        )}

        <ExpandableSection headerText="Scan targets" variant="footer"
          headerDescription="What the next refresh covers. Spoke accounts need the LifecycleTrackerScanRole (deployed by the Org stack's StackSet, or the Spoke stack by hand).">
          <SpaceBetween size="m">
            <FormField label="Accounts to scan">
              <Select
                selectedOption={SOURCE_OPTIONS.find((o) => o.value === source) || SOURCE_OPTIONS[0]}
                onChange={({ detail }) => setSource(detail.selectedOption.value as ScanTargetSource)}
                options={SOURCE_OPTIONS}
              />
            </FormField>
            {(source === 'manual' || source === 'organization' || source === 'ou') && (
              <FormField label={source === 'manual' ? 'Accounts to scan' : 'Fallback account list'}
                description={source === 'manual' ? 'Each account needs the spoke role.' : 'Used only when AWS Organizations denies access to the hub.'} stretch>
                <AttributeEditor
                  items={accounts}
                  addButtonText="Add account"
                  removeButtonText="Remove"
                  empty="No accounts listed."
                  onAddButtonClick={() => setAccounts([...accounts, { id: '', name: '' }])}
                  onRemoveButtonClick={({ detail }) => setAccounts(accounts.filter((_, i) => i !== detail.itemIndex))}
                  definition={[
                    {
                      label: 'Account id',
                      errorText: (item) => accountError(item.id),
                      control: (item, index) => (
                        <Input value={item.id} placeholder="222222222222" inputMode="numeric"
                          onChange={({ detail }) => setAccounts(accounts.map((a, i) => (i === index ? { ...a, id: detail.value } : a)))} />
                      ),
                    },
                    {
                      label: 'Name (optional)',
                      control: (item, index) => (
                        <Input value={item.name} placeholder="Account A"
                          onChange={({ detail }) => setAccounts(accounts.map((a, i) => (i === index ? { ...a, name: detail.value } : a)))} />
                      ),
                    },
                  ]}
                />
              </FormField>
            )}
            <ColumnLayout columns={2}>
              {source === 'ou' && (
                <TokenListField label="Root / OU ids" description="Accounts under these are scanned, recursively." tokens={ouIds} onChange={setOuIds}
                  placeholder="ou-abcd-12345678" validate={OU_ID} invalidText="is not an organization root or OU id" />
              )}
              {(source === 'organization' || source === 'ou') && (
                <TokenListField label="Exclude accounts" description="Never scanned even if listed by Organizations." tokens={excluded} onChange={setExcluded}
                  placeholder="444444444444" validate={ACCOUNT_ID} invalidText="is not a 12-digit account id" />
              )}
              <TokenListField label="Regions" description="Empty = the region the tracker is deployed in." tokens={regions} onChange={(r) => setRegions(r.map((x) => x.toLowerCase()))}
                placeholder={DEPLOY_REGION} validate={REGION} invalidText="is not a region code" />
            </ColumnLayout>
            <Box>
              <Button variant="primary" loading={saving} disabled={!accountsValid || (source === 'manual' && !accounts.some((a) => a.id.trim()))} onClick={save}>Save targets</Button>
              {coverage?.last_scan.accounts.length ? (
                <Box variant="small" color="text-body-secondary" display="inline" padding={{ left: 'm' }}>
                  Inventory currently holds resources from {coverage.last_scan.accounts.length} account{coverage.last_scan.accounts.length === 1 ? '' : 's'}
                  {coverage.last_scan.accounts.length <= 3 ? `: ${coverage.last_scan.accounts.map((id) => accountLabel(id, rows.find((a) => a.id === id)?.name)).join(', ')}` : ''}.
                </Box>
              ) : null}
            </Box>
          </SpaceBetween>
        </ExpandableSection>
      </SpaceBetween>
    </Container>
  );
}
