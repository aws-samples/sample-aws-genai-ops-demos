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
import Textarea from '@cloudscape-design/components/textarea';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import { ScanCoverage, ScanTargets, ScanTargetSource, EMPTY_TARGETS, saveScanTargets } from '../api';
import { accountLabel } from '../lifecycle';

// Multi-account coverage (#144): which accounts the last scan resolved and
// reached, and the editor of the _scan_targets control row. Single-account
// deployments see one covered account (the hub) and can leave the editor alone.

const SOURCE_OPTIONS: Array<{ value: ScanTargetSource; label: string; description: string }> = [
  { value: 'hub', label: 'This account only', description: 'Default. The account the tracker runs in.' },
  { value: 'organization', label: 'Whole organization', description: 'Every active account, listed with AWS Organizations.' },
  { value: 'ou', label: 'Organizational units', description: 'Accounts under the given root / OU ids, recursively.' },
  { value: 'manual', label: 'Account list', description: 'The accounts entered below; no Organizations access needed.' },
];

const splitList = (text: string): string[] => text.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
const parseAccounts = (text: string) =>
  text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean).map((l) => {
    const [id, ...rest] = l.split(/[\s,;]+/);
    return { id, name: rest.join(' ').trim() };
  });

interface Props {
  coverage: ScanCoverage | null;
  onSaved: (message: string, ok: boolean) => void;
}

export default function ScanTargetsPanel({ coverage, onSaved }: Props) {
  const resolved = coverage?.accounts || null;
  const targets = coverage?.targets || EMPTY_TARGETS;
  const [source, setSource] = useState<ScanTargetSource>(targets.source);
  const [accountsText, setAccountsText] = useState('');
  const [ouText, setOuText] = useState('');
  const [excludeText, setExcludeText] = useState('');
  const [regionsText, setRegionsText] = useState('');
  const [saving, setSaving] = useState(false);

  // (Re)fill the form when the stored targets arrive
  useEffect(() => {
    setSource(targets.source);
    setAccountsText(targets.accounts.map((a) => `${a.id} ${a.name}`.trim()).join('\n'));
    setOuText(targets.ou_ids.join(', '));
    setExcludeText(targets.exclude_accounts.join(', '));
    setRegionsText(targets.regions.join(', '));
  }, [coverage]);

  const scanned = new Set(resolved?.accounts_scanned || []);
  const failed = new Set(resolved?.accounts_failed || []);
  const rows = resolved?.accounts || [];

  const save = async () => {
    setSaving(true);
    const next: ScanTargets = {
      source,
      accounts: parseAccounts(accountsText),
      ou_ids: splitList(ouText),
      exclude_accounts: splitList(excludeText),
      regions: splitList(regionsText).map((r) => r.toLowerCase()),
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
            <ColumnLayout columns={2}>
              {source === 'ou' && (
                <FormField label="Root / OU ids" description="Comma-separated, e.g. r-abcd, ou-abcd-12345678" stretch>
                  <Input value={ouText} onChange={({ detail }) => setOuText(detail.value)} placeholder="ou-abcd-12345678" />
                </FormField>
              )}
              {(source === 'manual' || source === 'organization' || source === 'ou') && (
                <FormField label={source === 'manual' ? 'Accounts' : 'Fallback account list'}
                  description={source === 'manual' ? 'One per line: account id, then an optional name' : 'Used when Organizations denies access. One per line: id, optional name'} stretch>
                  <Textarea value={accountsText} onChange={({ detail }) => setAccountsText(detail.value)} rows={3}
                    placeholder={'222222222222 Account A\n333333333333 Account B'} />
                </FormField>
              )}
              {(source === 'organization' || source === 'ou') && (
                <FormField label="Exclude accounts" description="Comma-separated account ids" stretch>
                  <Input value={excludeText} onChange={({ detail }) => setExcludeText(detail.value)} placeholder="444444444444" />
                </FormField>
              )}
              <FormField label="Regions" description="Comma-separated; empty = the deployment region" stretch>
                <Input value={regionsText} onChange={({ detail }) => setRegionsText(detail.value)} placeholder="eu-central-1, us-east-1" />
              </FormField>
            </ColumnLayout>
            <Box>
              <Button variant="primary" loading={saving} onClick={save}>Save targets</Button>
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
