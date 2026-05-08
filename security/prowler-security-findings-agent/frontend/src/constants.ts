export const SEVERITIES = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'] as const;
export type Severity = typeof SEVERITIES[number];

export const SEVERITY_COLOR: Record<string, string> = {
  CRITICAL: '#d91515',
  HIGH: '#ff6b00',
  MEDIUM: '#e8a317',
  LOW: '#037f0c',
  INFO: '#5f6b7a',
  UNKNOWN: '#8b949e',
};

export const STATUS_LABELS: Record<string, string> = {
  FAIL: 'Failing',
  PASS: 'Passing',
  MANUAL: 'Manual review',
  UNKNOWN: 'Unknown',
};
