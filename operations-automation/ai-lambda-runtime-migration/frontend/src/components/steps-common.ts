// Shared by the two Steps cards (ScanSteps for a scan, MigrationSteps for a
// migration batch) so a status label or a unit plural can never differ between
// them.
import type { RefreshPhase } from '../api';

export type PhaseStatus = RefreshPhase['status'];

// Status icon aria-labels for Cloudscape Steps.
export const STATUS_LABEL: Record<PhaseStatus, string> = {
  pending: 'Pending',
  'in-progress': 'In progress',
  success: 'Done',
  warning: 'Done with failures',
  error: 'Failed',
  stopped: 'Skipped',
};

// "1 function" / "2 functions".
export const countUnit = (n: number, unit: string): string => `${unit}${n === 1 ? '' : 's'}`;
