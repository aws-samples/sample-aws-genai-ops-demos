import Steps, { StepsProps } from '@cloudscape-design/components/steps';
import ProgressBar from '@cloudscape-design/components/progress-bar';
import Box from '@cloudscape-design/components/box';
import Icon from '@cloudscape-design/components/icon';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import LiveRegion from '@cloudscape-design/components/live-region';
import { ScanPhase } from '../api';
import { STATUS_LABEL, countUnit } from './steps-common';

// Progress of a scan (Discover + Assess) as Cloudscape "progressive steps": one
// step per pipeline phase (Prepare / Discover / Assess / Summarize), a progress
// bar on the phase in flight when its size is known (functions to discover /
// assess). The phases come from the durable execution history, so what is shown
// is what the service checkpointed. Once the run is over the same steps stay
// available, collapsed (pattern: "include an expandable section detailing the
// steps taken").

// Discover and Assess are the generative-AI phases: Amazon Nova prioritizes the
// fleet and assesses each function.
const isGenAi = (p: ScanPhase) =>
  p.label.startsWith('Discover') || p.label.startsWith('Assess');

// What a phase's counter counts. The fan-out steps iterate over different units:
// Assess runs one unit per Lambda function; the Support-tier phase runs one unit
// per AWS account; Discover runs one unit per TA-ELIGIBLE account (the Basic/
// Developer accounts were ruled out by the Support-tier step), so it reads
// "eligible accounts" to make that subset explicit. Labelling the count removes
// the "2 of 2 done" ambiguity (2 accounts? 2 functions?).
const unitFor = (p: ScanPhase): string =>
  p.label.startsWith('Assess') ? 'function'
    : p.label.startsWith('Discover') ? 'eligible account'
    : 'account';
// "5 of 5 accounts" / "2 of 2 eligible accounts" / "63 of 63 functions"
const countText = (p: ScanPhase): string => `${p.done} of ${p.total} ${countUnit(p.total ?? 0, unitFor(p))}`;

// Per-phase sub-note surfacing WHICH service/action the phase uses, appended to
// the progress detail: Discover queries Trusted Advisor then Nova prioritizes;
// Assess runs the Nova code analysis. Keeps the service call visible in the
// sub-label rather than crowding the step title.
const phaseNote = (p: ScanPhase): string =>
  p.label.startsWith('Discover') ? ' · via Trusted Advisor, then Amazon Nova prioritizes'
    : p.label.startsWith('Assess') ? ' · Amazon Nova assesses complexity'
    : '';

// The "Determine the accounts to scan" phase is a single poll of AWS
// Organizations, so "1 of 1 account done" tells the user nothing. It reports a
// result instead: how many accounts the scan found.
const isAccountListing = (p: ScanPhase) => p.label.startsWith('Determine the accounts');

const detailsFor = (p: ScanPhase) => {
  if (isAccountListing(p)) {
    if (p.status === 'in-progress') return <Box variant="small">Listing accounts…</Box>;
    if ((p.status === 'success' || p.status === 'warning') && typeof p.accountsFound === 'number') {
      return <Box variant="small" color="text-body-secondary">{p.accountsFound} {countUnit(p.accountsFound, 'account')} found</Box>;
    }
    if (p.status === 'stopped') return <Box variant="small" color="text-body-secondary">Not part of this run</Box>;
    if (p.status === 'success' || p.status === 'warning') return <Box variant="small" color="text-body-secondary">Done</Box>;
    return undefined;
  }
  // A sized fan-out phase (support/discover/assess) shows the rich progress bar
  // even when it has a single unit: total is derived from the checkpointed step
  // count, so total === 1 is a real "1 of 1", not the unknown-size case. Only a
  // phase with no known size (total null) falls back to a bare "Running".
  if (p.status === 'in-progress' && p.total && p.total >= 1) {
    const pct = Math.round(((p.done + p.failed) / p.total) * 100);
    // The "running: ..." list used to dump raw internal cell ids (e.g.
    // "hub-eu-west-3") that mean nothing to a user; the "N of M done" bar is the
    // real signal, so only the phase note is appended.
    const info = `${countText(p)} done${p.failed ? `, ${p.failed} failed` : ''}${phaseNote(p)}`;
    return <ProgressBar value={pct} additionalInfo={info} ariaLabel={`${p.label}: ${info}`} />;
  }
  if (p.status === 'in-progress') return <Box variant="small">Running</Box>;
  if (p.total && p.total >= 1 && p.status !== 'pending' && p.status !== 'stopped') {
    return <Box variant="small" color={p.failed ? 'text-status-warning' : 'text-body-secondary'}>{countText(p)} done{p.failed ? `, ${p.failed} failed` : ''}{phaseNote(p)}</Box>;
  }
  if (p.status === 'stopped') return <Box variant="small" color="text-body-secondary">Not part of this run</Box>;
  return undefined;
};

export default function ScanSteps({ phases, running }: { phases: ScanPhase[]; running: boolean }) {
  const steps: StepsProps.Step[] = phases.map((p) => ({
    status: p.status === 'in-progress' ? (p.total && p.total >= 1 ? 'in-progress' : 'loading') : p.status,
    statusIconAriaLabel: STATUS_LABEL[p.status],
    // Discover + Assess are the generative AI phases: sparkle icon (Cloudscape gen AI iconography)
    header: isGenAi(p) ? <><Icon name="gen-ai" size="small" ariaLabel="Generative AI" /> {p.label}</> : p.label,
    details: detailsFor(p),
  }));
  const current = phases.find((p) => p.status === 'in-progress');
  const done = phases.filter((p) => p.status === 'success' || p.status === 'warning').length;
  const failed = phases.filter((p) => p.status === 'error').length;

  if (!running) {
    return (
      <ExpandableSection variant="container" headerText={`Last refresh: ${done} of ${phases.length} steps done${failed ? `, ${failed} failed` : ''}`}>
        <Steps steps={steps} ariaLabel="Last refresh steps" />
      </ExpandableSection>
    );
  }
  return (
    <Container
      header={
        <Header variant="h3" description="Runs server-side: you can navigate away, the page re-attaches when you come back.">
          Scan in progress
        </Header>
      }
    >
      <LiveRegion>{current ? `${current.label}: ${!isAccountListing(current) && current.total ? `${countText(current)} done` : 'running'}` : ''}</LiveRegion>
      <Steps steps={steps} ariaLabel="Scan progress" />
    </Container>
  );
}
