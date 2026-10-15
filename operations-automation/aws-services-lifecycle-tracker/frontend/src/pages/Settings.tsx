import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Container from '@cloudscape-design/components/container';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Box from '@cloudscape-design/components/box';
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar';
import Alert from '@cloudscape-design/components/alert';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Multiselect from '@cloudscape-design/components/multiselect';
import TokenGroup from '@cloudscape-design/components/token-group';
import Button from '@cloudscape-design/components/button';
import Link from '@cloudscape-design/components/link';
import FormField from '@cloudscape-design/components/form-field';
import { getScanners, listRegions, saveScanTargets, ScanCoverage, ScanTargets, ScanTargetSource, EMPTY_TARGETS, MAX_REGIONS } from '../api';
import { InfoLink } from '../help';

// Settings (#144): what the next refresh covers. The account scope is decided
// at deploy time (deploy-all -MultiAccount [-OrgTargets ...] writes the
// _scan_targets row) and is shown read-only here, next to the last-scan
// numbers. The ONE setting this page edits is the list of regions to scan;
// the save keeps every other stored target field as is.

const SCOPE_LABEL: Record<ScanTargetSource, string> = {
  hub: 'This account only',
  organization: 'Whole organization',
  ou: 'Organizational units',
  manual: 'Account list',
};

const scopeText = (t: ScanTargets): string => {
  const label = SCOPE_LABEL[t.source] || t.source;
  if (t.source === 'ou') return `${label} (${t.ou_ids.length})`;
  if (t.source === 'manual') return `${label} (${t.accounts.length})`;
  return label;
};

// Shown while list_regions has not answered yet: the region this build was made for (set by scripts/build-frontend), never a literal
const DEPLOY_REGION_FALLBACK: string = (import.meta as any).env?.VITE_REGION || 'the deploy region';

const UNREACHABLE_SHOWN = 10;

// Display-only friendly names for commercial region codes (cosmetic labels only;
// which codes are actually selectable always comes from listRegions() / ec2:DescribeRegions,
// never from this map, so a region AWS adds shows up as its code until this list is updated).
const REGION_NAMES: Record<string, string> = {
  'us-east-1': 'US East (N. Virginia)',
  'us-east-2': 'US East (Ohio)',
  'us-west-1': 'US West (N. California)',
  'us-west-2': 'US West (Oregon)',
  'af-south-1': 'Africa (Cape Town)',
  'ap-east-1': 'Asia Pacific (Hong Kong)',
  'ap-east-2': 'Asia Pacific (Taipei)',
  'ap-south-1': 'Asia Pacific (Mumbai)',
  'ap-south-2': 'Asia Pacific (Hyderabad)',
  'ap-southeast-1': 'Asia Pacific (Singapore)',
  'ap-southeast-2': 'Asia Pacific (Sydney)',
  'ap-southeast-3': 'Asia Pacific (Jakarta)',
  'ap-southeast-4': 'Asia Pacific (Melbourne)',
  'ap-southeast-5': 'Asia Pacific (Malaysia)',
  'ap-southeast-7': 'Asia Pacific (Thailand)',
  'ap-northeast-1': 'Asia Pacific (Tokyo)',
  'ap-northeast-2': 'Asia Pacific (Seoul)',
  'ap-northeast-3': 'Asia Pacific (Osaka)',
  'ca-central-1': 'Canada (Central)',
  'ca-west-1': 'Canada West (Calgary)',
  'eu-central-1': 'Europe (Frankfurt)',
  'eu-central-2': 'Europe (Zurich)',
  'eu-west-1': 'Europe (Ireland)',
  'eu-west-2': 'Europe (London)',
  'eu-west-3': 'Europe (Paris)',
  'eu-north-1': 'Europe (Stockholm)',
  'eu-south-1': 'Europe (Milan)',
  'eu-south-2': 'Europe (Spain)',
  'il-central-1': 'Israel (Tel Aviv)',
  'me-central-1': 'Middle East (UAE)',
  'me-south-1': 'Middle East (Bahrain)',
  'mx-central-1': 'Mexico (Central)',
  'sa-east-1': 'South America (São Paulo)',
};

// Friendly label with the code always visible, e.g. "US East (N. Virginia) (us-east-1)".
// Falls back to the bare code for a region this map does not know about yet.
const regionLabel = (code: string): string => (REGION_NAMES[code] ? `${REGION_NAMES[code]} (${code})` : code);

export default function Settings() {
  const navigate = useNavigate();
  const [coverage, setCoverage] = useState<ScanCoverage | null>(null);
  const [regionList, setRegionList] = useState<{ options: string[]; deployRegion: string } | null>(null);
  const [regionsError, setRegionsError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [flashbarItems, setFlashbarItems] = useState<FlashbarProps.MessageDefinition[]>([]);

  const flash = (type: 'success' | 'error', content: string) =>
    setFlashbarItems([{ type, dismissible: true, dismissLabel: 'Dismiss', onDismiss: () => setFlashbarItems([]), content, id: `${type}-${Date.now()}` }]);

  const loadCoverage = () =>
    getScanners()
      .then(setCoverage)
      .catch((err: any) => flash('error', `Failed to load scan settings: ${err.message}`));

  // The two loads are independent: a failing region list must not hide the facts.
  useEffect(() => {
    loadCoverage();
    listRegions()
      .then((res) => {
        if (res.success && res.regions) {
          setRegionList({ options: res.regions, deployRegion: res.deploy_region || DEPLOY_REGION_FALLBACK });
        } else {
          setRegionsError(res.error || 'Unknown error');
        }
      })
      .catch((err: any) => setRegionsError(err.message));
  }, []);

  // (Re)fill the selection when the stored targets arrive
  useEffect(() => {
    if (coverage) setSelected(coverage.targets.regions);
  }, [coverage]);

  const targets = coverage?.targets || EMPTY_TARGETS;
  const resolved = coverage?.accounts || null;
  const deployRegion = regionList?.deployRegion || DEPLOY_REGION_FALLBACK;

  const save = async () => {
    if (!coverage) return;
    setSaving(true);
    try {
      // The deploy region is always scanned and cannot be removed, so it is
      // always persisted first; the Multiselect only edits the OTHER regions.
      // (deployRegion comes from listRegions()/the build, never a hardcoded code.)
      const regions = [deployRegion, ...selected.filter((r) => r !== deployRegion)];
      const res = await saveScanTargets({ ...coverage.targets, regions });
      if (res.success) {
        flash('success', 'Regions saved. They apply to the next refresh.');
        loadCoverage();
      } else {
        flash('error', `Could not save regions: ${res.error}`);
      }
    } catch (e: any) {
      flash('error', `Could not save regions: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  // --- Scan scope facts -------------------------------------------------------
  const scanners = coverage?.scanners.length ?? 0;
  const nextRefreshSteps = resolved ? resolved.accounts.length * Math.max(targets.regions.length, 1) * scanners : null;

  // --- Last scan facts --------------------------------------------------------
  const unreachable = resolved?.accounts_failed || [];
  const byAccount = coverage?.health?.by_account || {};
  const healthAnswered = Object.values(byAccount).filter((s) => s.health_available).length;
  const healthTotal = Object.keys(byAccount).length || resolved?.accounts.length || 0;

  // --- Regions editor -------------------------------------------------------
  // The deploy region is pinned (always scanned, not removable) and shown as a
  // read-only token; the Multiselect edits only the OTHER regions. Stored
  // regions missing from the enabled list (e.g. written by hand) stay visible
  // and removable. The pinned deploy region counts against MAX_REGIONS.
  const additionalSelected = selected.filter((r) => r !== deployRegion);
  const optionCodes = regionList
    ? [...new Set([...regionList.options, ...additionalSelected])].filter((c) => c !== deployRegion).sort()
    : [];
  const atCap = additionalSelected.length + 1 >= MAX_REGIONS;

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          info={<InfoLink />}
          description="What the next refresh covers. The account scope is set when the tracker is deployed; the regions are chosen here."
        >
          Settings
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Flashbar items={flashbarItems} stackItems />

        <Container header={<Header variant="h2" description="Decided at deploy time. Re-run the deploy script to change it.">Scan scope</Header>}>
          <KeyValuePairs
            columns={4}
            ariaLabel="Scan scope"
            items={[
              {
                label: 'Accounts',
                value: (
                  <SpaceBetween size="xxxs">
                    <Box>{scopeText(targets)}</Box>
                    <Box variant="small" color="text-body-secondary">{resolved ? resolved.accounts.length : '—'} resolved</Box>
                  </SpaceBetween>
                ),
              },
              {
                label: 'Set at deploy',
                value: (
                  <SpaceBetween size="xxxs">
                    <Box variant="code">.\deploy-all.ps1 -MultiAccount [-OrgTargets ...]</Box>
                    <Box variant="code">./deploy-all.sh --multi-account [--org-targets ...]</Box>
                  </SpaceBetween>
                ),
              },
              {
                label: 'Regions',
                value: targets.regions.length ? targets.regions.map(regionLabel).join(', ') : `deploy region (${regionLabel(deployRegion)})`,
              },
              {
                label: 'AWS services scanned',
                value: coverage ? (
                  <SpaceBetween size="xxxs">
                    <Link onFollow={(e) => { e.preventDefault(); navigate('/services'); }} href="#">{scanners}</Link>
                    <Box variant="small" color="text-body-secondary">Services whose resources are discovered on your account; see Sources &amp; coverage for the list</Box>
                  </SpaceBetween>
                ) : '—',
              },
              {
                label: 'Next refresh',
                value: nextRefreshSteps !== null ? (
                  <SpaceBetween size="xxxs">
                    <Box>≈ {nextRefreshSteps} steps</Box>
                    <Box variant="small" color="text-body-secondary">accounts × regions × scanners, one durable step each</Box>
                  </SpaceBetween>
                ) : 'known after the first refresh',
              },
            ]}
          />
        </Container>

        <Container header={<Header variant="h2">Last scan</Header>}>
          {!resolved ? (
            <Box color="text-body-secondary">No scan has run yet. Click Refresh on the dashboard to run one.</Box>
          ) : (
            <SpaceBetween size="m">
              <KeyValuePairs
                columns={4}
                ariaLabel="Last scan"
                items={[
                  {
                    label: 'When',
                    value: (resolved.scanned_at ?? resolved.resolved_at) ? new Date((resolved.scanned_at ?? resolved.resolved_at) as string).toLocaleString() : '—',
                  },
                  { label: 'Accounts covered', value: resolved.accounts_scanned?.length ?? 0 },
                  {
                    label: 'Accounts unreachable',
                    value: unreachable.length > 0 ? (
                      <SpaceBetween size="xxxs">
                        <StatusIndicator type="error">{unreachable.length}</StatusIndicator>
                        <Box variant="small">
                          {unreachable.slice(0, UNREACHABLE_SHOWN).join(', ')}
                          {unreachable.length > UNREACHABLE_SHOWN ? ` and ${unreachable.length - UNREACHABLE_SHOWN} more` : ''}
                        </Box>
                        <Box variant="small" color="text-body-secondary">spoke role missing or not trusting this hub</Box>
                      </SpaceBetween>
                    ) : (
                      <StatusIndicator type="success">0</StatusIndicator>
                    ),
                  },
                  { label: 'Resources', value: coverage?.last_scan.resources ?? 0 },
                  {
                    label: 'Health cross-checked',
                    value: (
                      <SpaceBetween size="xxxs">
                        <Box>{healthAnswered} of {healthTotal} accounts</Box>
                        <Box variant="small" color="text-body-secondary">needs Business or Enterprise Support in each account</Box>
                      </SpaceBetween>
                    ),
                  },
                ]}
              />
              {resolved.errors?.map((e, i) => (
                <StatusIndicator key={i} type="warning">{e}</StatusIndicator>
              ))}
              <Link onFollow={(e) => { e.preventDefault(); navigate('/dashboard'); }} href="#">
                Per-account detail: Dashboard, By account tab
              </Link>
            </SpaceBetween>
          )}
        </Container>

        <Container header={<Header variant="h2" description="The only setting you change here.">Regions to scan</Header>}>
          {regionsError ? (
            <SpaceBetween size="m">
              <Alert type="error" header="Could not list the regions enabled in this account">{regionsError}</Alert>
              {targets.regions.length ? (
                <TokenGroup items={targets.regions.map((r) => ({ label: regionLabel(r) }))} readOnly />
              ) : (
                <Box color="text-body-secondary">deploy region ({regionLabel(deployRegion)})</Box>
              )}
            </SpaceBetween>
          ) : regionList === null ? (
            <Multiselect
              statusType="loading"
              loadingText="Loading the regions enabled in this account"
              options={[]}
              selectedOptions={[]}
              placeholder="Deploy region only"
              disabled
            />
          ) : (
            <SpaceBetween size="m">
              <FormField
                label="Regions"
                constraintText={`The deploy region (${regionLabel(deployRegion)}) is always scanned and cannot be removed. Add up to ${MAX_REGIONS - 1} more. Applies to the next refresh.`}
                stretch
              >
                <SpaceBetween size="xs">
                  {/* Deploy region: always scanned, pinned. A TokenGroup with no
                      onDismiss renders no remove control, so this token cannot be
                      removed. deployRegion is resolved from listRegions()/the build,
                      never a hardcoded code. */}
                  <TokenGroup
                    items={[{ label: regionLabel(deployRegion), description: 'Deploy region — always scanned' }]}
                  />
                  {/* Multiselect edits only the OTHER regions; the deploy region is
                      excluded from both the options and the selection it manages. */}
                  <Multiselect
                    filteringType="auto"
                    placeholder="Add more regions"
                    selectedAriaLabel="Selected"
                    deselectAriaLabel={(o) => `Remove ${o.label}`}
                    options={optionCodes.map((code) => ({ label: regionLabel(code), value: code, disabled: atCap && !additionalSelected.includes(code) }))}
                    selectedOptions={additionalSelected.map((code) => ({ label: regionLabel(code), value: code }))}
                    onChange={({ detail }) => setSelected(detail.selectedOptions.map((o) => o.value!))}
                  />
                </SpaceBetween>
              </FormField>
              <Button variant="primary" loading={saving} disabled={!coverage} onClick={save}>Save regions</Button>
            </SpaceBetween>
          )}
        </Container>
      </SpaceBetween>
    </ContentLayout>
  );
}
