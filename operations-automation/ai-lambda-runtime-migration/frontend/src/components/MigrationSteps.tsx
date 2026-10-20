import Steps, { StepsProps } from '@cloudscape-design/components/steps';
import ProgressBar from '@cloudscape-design/components/progress-bar';
import Box from '@cloudscape-design/components/box';
import Icon from '@cloudscape-design/components/icon';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import LiveRegion from '@cloudscape-design/components/live-region';
import { MigrationPhase, MigrationBatchSummary } from '../api';
import { migrationSummaryLine } from '../constants';
import { STATUS_LABEL, countUnit } from './steps-common';

// Progress of one batch migration as Cloudscape "progressive steps": one step
// per migration phase (Prepare / Resolve / Transform), a progress bar on the
// phase in flight when its size is known. Reads identically whether migrating
// 1 or 100 functions — the counts flow left to right and rejections show as
// the gap. A thin sibling of ScanSteps: same render structure, same
// Cloudscape components, but its own copy (migration units, no ellipsis).
//
// The card is ONE ExpandableSection whatever the run state. The page owns
// `expanded`: it opens the card on a Migrate click, collapses it once nothing
// but builds is in flight, re-attaches to the latest batch collapsed on page
// load, and the user can toggle it at any time. The flow ends with a pushed
// upgrade branch per function; there is no fourth phase.

// Phase classification by label prefix (the labels are the backend contract:
// api._MIGRATION_PHASE_LABELS).
const isPrepare = (p: MigrationPhase) => p.label.startsWith('Prepare');
const isResolve = (p: MigrationPhase) => p.label.startsWith('Resolve');
const isTransform = (p: MigrationPhase) => p.label.startsWith('Transform');

// The Transform phase is the generative-AI phase: AWS Transform upgrades the
// code. Mark it with the gen-AI sparkle, not the bookkeeping phases around it.
const isGenAi = isTransform;

const plural = (n: number, one: string, many: string): string => (n === 1 ? one : many);

const branchesReadyOf = (p: MigrationPhase): number => p.branchesReady ?? Math.max(0, p.done - (p.noChanges ?? 0));

// What a phase's counter counts and how a non-success reads. The user's model:
// counts flow left to right (each phase works on the survivors of the previous)
// and the gap is a REJECTION, not a failure — a function with no repository tag
// is triaged out, nothing broke. Resolve therefore says "rejected"; Transform
// says "failed" (a real error). A transform that ran and found nothing to
// change is neither: it reads as "no changes".
//   Resolve:   "0 of 2 functions resolved, 2 rejected"
//   Transform: "85 of 90 functions with a branch ready, 3 no changes, 2 failed"
// Transform folds its failed segment into countText, so gapText only adds the
// Resolve rejections (no double "failed").
const countText = (p: MigrationPhase): string => {
  const total = p.total ?? 0;
  if (isResolve(p)) return `${p.done} of ${total} ${countUnit(total, 'function')} resolved`;
  if (isTransform(p)) {
    const noChanges = p.noChanges ?? 0;
    return `${branchesReadyOf(p)} of ${total} ${countUnit(total, 'function')} with a branch ready`
      + (noChanges ? `, ${noChanges} no changes` : '')
      + (p.failed ? `, ${p.failed} failed` : '');
  }
  return `${p.done} of ${total} prepared`;
};
const gapText = (p: MigrationPhase): string =>
  (isResolve(p) && p.failed) ? `, ${p.failed} rejected` : '';

// Settled Transform detail: "N branches ready, K no changes, J failed"
// (zero segments omitted).
const transformSettledText = (p: MigrationPhase): string => {
  const ready = branchesReadyOf(p);
  const noChanges = p.noChanges ?? 0;
  const segments: string[] = [];
  if (ready) segments.push(`${ready} ${plural(ready, 'branch', 'branches')} ready`);
  if (noChanges) segments.push(`${noChanges} no changes`);
  if (p.failed) segments.push(`${p.failed} failed`);
  return segments.join(', ') || 'Nothing to transform';
};

const detailsFor = (p: MigrationPhase) => {
  // Prepare is the single bookkeeping step that admits the selection into the
  // batch; "1 of 1 function prepared" is meaningless, so it reports the batch
  // size it admitted instead (the Resolve phase's total).
  if (isPrepare(p)) {
    if (p.status === 'in-progress') return <Box variant="small">Preparing</Box>;
    if (p.status === 'error') return <Box variant="small" color="text-status-error">Could not prepare the batch</Box>;
    return undefined;
  }
  if (p.status === 'in-progress' && p.total && p.total >= 1) {
    const pct = Math.round(((p.done + p.failed) / p.total) * 100);
    const info = `${countText(p)}${gapText(p)}`;
    return <ProgressBar value={pct} additionalInfo={info} ariaLabel={`${p.label}: ${info}`} />;
  }
  if (p.status === 'in-progress') return <Box variant="small">Running</Box>;
  if (p.total && p.total >= 1 && p.status !== 'pending' && p.status !== 'stopped') {
    return (
      <Box variant="small" color={p.failed ? 'text-status-warning' : 'text-body-secondary'}>
        {isTransform(p) ? transformSettledText(p) : `${countText(p)}${gapText(p)}`}
      </Box>
    );
  }
  // A downstream phase with nothing to work on (everything upstream was
  // rejected or failed, a run that ended early) must say so rather than sit as
  // a bare label. The backend owns the wording (p.note).
  if (p.status === 'stopped' || (p.total === 0 && p.status !== 'pending')) {
    return (
      <Box variant="small" color="text-body-secondary">
        {p.note ?? 'Not part of this run'}
      </Box>
    );
  }
  return undefined;
};

const timeOf = (d?: Date): string =>
  d ? d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : '';

// Header while the batch runs, read from the phase counters:
// "Migration in progress: 2 selected, 1 rejected, 1 transformation running, started 10:38".
const runningHeader = (phases: MigrationPhase[], totalSelected: number | undefined, startDate?: Date): string => {
  const resolve = phases.find(isResolve);
  const transform = phases.find(isTransform);
  const selected = totalSelected ?? resolve?.total ?? 0;
  const rejected = resolve?.failed ?? 0;
  const runningCount = Math.max(0, (transform?.total ?? 0) - (transform?.done ?? 0) - (transform?.failed ?? 0));
  const ready = transform ? branchesReadyOf(transform) : 0;
  const segments = [`${selected} selected`];
  if (rejected) segments.push(`${rejected} rejected`);
  segments.push(`${runningCount} ${plural(runningCount, 'transformation', 'transformations')} running`);
  if (ready) segments.push(`${ready} ${plural(ready, 'branch', 'branches')} ready`);
  const started = timeOf(startDate);
  return `Migration in progress: ${segments.join(', ')}${started ? `, started ${started}` : ''}`;
};

// Header once the batch ended. The durable Result (summary) is the source when
// the execution SUCCEEDED; the phases cover FAILED / STOPPED executions that
// have no Result.
const finishedHeader = (phases: MigrationPhase[], summary?: MigrationBatchSummary, totalSelected?: number): string => {
  if (summary) return `Last migration: ${migrationSummaryLine(summary)}`;
  const resolve = phases.find(isResolve);
  const transform = phases.find(isTransform);
  const segments = [`${totalSelected ?? resolve?.total ?? 0} selected`];
  if (resolve?.failed) segments.push(`${resolve.failed} rejected`);
  if (transform) {
    const ready = branchesReadyOf(transform);
    if (ready) segments.push(`${ready} ${plural(ready, 'branch', 'branches')} ready`);
    if (transform.noChanges) segments.push(`${transform.noChanges} no changes`);
    if (transform.failed) segments.push(`${transform.failed} failed`);
  }
  return `Last migration: ${segments.join(', ')}`;
};

export default function MigrationSteps({
  phases, running, summary, startDate, totalSelected, expanded, onToggle,
}: {
  phases: MigrationPhase[];
  running: boolean;
  summary?: MigrationBatchSummary;
  startDate?: Date;
  totalSelected?: number;
  expanded: boolean;
  onToggle: (open: boolean) => void;
}) {
  const steps: StepsProps.Step[] = phases.map((p) => ({
    status: p.status === 'in-progress' ? (p.total && p.total >= 1 ? 'in-progress' : 'loading') : p.status,
    statusIconAriaLabel: STATUS_LABEL[p.status],
    header: isGenAi(p) ? <><Icon name="gen-ai" size="small" ariaLabel="Generative AI" /> {p.label}</> : p.label,
    details: detailsFor(p),
  }));
  const current = phases.find((p) => p.status === 'in-progress');
  const headerText = running
    ? runningHeader(phases, totalSelected, startDate)
    : finishedHeader(phases, summary, totalSelected);

  return (
    <ExpandableSection
      variant="container"
      headerText={headerText}
      headerDescription={running ? 'Runs server-side: you can navigate away, the page re-attaches when you come back.' : undefined}
      expanded={expanded}
      onChange={({ detail }) => onToggle(detail.expanded)}
    >
      {running && (
        <LiveRegion>{current ? `${current.label}: ${current.total ? countText(current) : 'running'}` : ''}</LiveRegion>
      )}
      <Steps steps={steps} ariaLabel={running ? 'Migration progress' : 'Last migration steps'} />
    </ExpandableSection>
  );
}
