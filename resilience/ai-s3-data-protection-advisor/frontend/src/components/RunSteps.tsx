import Steps from '@cloudscape-design/components/steps';
import type { Step } from '../api';
import { stepStatus } from '../lib/runs';

const ARIA = { pending: 'Pending', 'in-progress': 'In progress', success: 'Done', error: 'Failed' } as const;

export default function RunSteps({ steps }: { steps: Step[] }) {
  return (
    <Steps
      ariaLabel="Scan steps"
      steps={steps.map((s) => {
        const st = stepStatus(s.status);
        return {
          status: st === 'in-progress' ? 'loading' : st,
          statusIconAriaLabel: ARIA[st],
          header: s.label,
          details: s.detail ?? undefined,
        };
      })}
    />
  );
}
