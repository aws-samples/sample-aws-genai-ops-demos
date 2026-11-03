import Steps, { StepsProps } from '@cloudscape-design/components/steps';
import ProgressBar from '@cloudscape-design/components/progress-bar';
import Box from '@cloudscape-design/components/box';
import Icon from '@cloudscape-design/components/icon';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import LiveRegion from '@cloudscape-design/components/live-region';
import { RefreshPhase } from '../api';

// Progress of a refresh run as Cloudscape "progressive steps": one step per
// pipeline phase, a progress bar on the phase in flight when its size is
// known (services to extract, cells to scan). The phases come from the durable
// execution history, so what is shown is what the service checkpointed.
// Once the run is over the same steps stay available, collapsed (pattern:
// "include an expandable section detailing the steps taken").

const STATUS_LABEL: Record<RefreshPhase['status'], string> = {
  pending: 'Pending', 'in-progress': 'In progress', success: 'Done', warning: 'Done with failures', error: 'Failed', stopped: 'Skipped',
};

// The phase where Amazon Bedrock reads the documentation pages (label set by the API)
const isGenAi = (p: RefreshPhase) => p.label.startsWith('Update the catalog');
const detailsFor = (p: RefreshPhase) => {
  if (p.status === 'in-progress' && p.total && p.total > 1) {
    const pct = Math.round(((p.done + p.failed) / p.total) * 100);
    const info = `${p.done} of ${p.total} done${p.failed ? `, ${p.failed} failed` : ''}${p.running.length ? ` · running: ${p.running.join(', ')}` : ''}${isGenAi(p) ? ' · Amazon Bedrock reads the documentation pages' : ''}`;
    return <ProgressBar value={pct} additionalInfo={info} ariaLabel={`${p.label}: ${info}`} />;
  }
  if (p.status === 'in-progress') return <Box variant="small">Running</Box>;
  if (p.total && p.total > 1 && p.status !== 'pending' && p.status !== 'stopped') {
    return <Box variant="small" color={p.failed ? 'text-status-warning' : 'text-body-secondary'}>{p.done} of {p.total} done{p.failed ? `, ${p.failed} failed` : ''}</Box>;
  }
  if (p.status === 'stopped') return <Box variant="small" color="text-body-secondary">Not part of this run</Box>;
  return undefined;
};

export default function RefreshSteps({ phases, running }: { phases: RefreshPhase[]; running: boolean }) {
  const steps: StepsProps.Step[] = phases.map((p) => ({
    status: p.status === 'in-progress' ? (p.total && p.total > 1 ? 'in-progress' : 'loading') : p.status,
    statusIconAriaLabel: STATUS_LABEL[p.status],
    // The catalog phase is the generative AI one: sparkle icon (Cloudscape gen AI iconography)
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
          Refresh in progress
        </Header>
      }
    >
      <LiveRegion>{current ? `${current.label}: ${current.done}${current.total ? ` of ${current.total}` : ''} done` : ''}</LiveRegion>
      <Steps steps={steps} ariaLabel="Refresh progress" />
    </Container>
  );
}
