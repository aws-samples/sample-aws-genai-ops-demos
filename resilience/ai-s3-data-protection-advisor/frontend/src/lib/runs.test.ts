import { describe, expect, it } from 'vitest';
import { accountOutcome, isActive, recsLabel, runStatus, stepStatus } from './runs';
import type { Run } from '../api';

const run = (status: Run['status'], extra: Partial<Run> = {}): Run => ({
  run_id: 'r', status, started_at: '2026-10-01T09:00:00Z', ...extra,
});

describe('runStatus', () => {
  it('maps the three statuses', () => {
    expect(runStatus(run('RUNNING'))).toEqual({ type: 'in-progress', label: 'Running' });
    expect(runStatus(run('SUCCEEDED'))).toEqual({ type: 'success', label: 'Completed' });
    expect(runStatus(run('FAILED'))).toEqual({ type: 'error', label: 'Failed' });
  });
  it('treats an unknown status as Unconfirmed', () => {
    expect(runStatus({ ...run('RUNNING'), status: 'X' as Run['status'] })).toEqual({ type: 'pending', label: 'Unconfirmed' });
  });
});

describe('recsLabel', () => {
  it('formats H / M / L', () => {
    expect(recsLabel({ HIGH: 4, MEDIUM: 11, LOW: 2 })).toBe('4 / 11 / 2');
    expect(recsLabel({ HIGH: 0, MEDIUM: 0, LOW: 0 })).toBe('0 / 0 / 0');
  });
  it('is a dash when unknown', () => {
    expect(recsLabel(undefined)).toBe('-');
  });
});

describe('isActive', () => {
  it('is true only while running', () => {
    expect(isActive(run('RUNNING'))).toBe(true);
    expect(isActive(run('SUCCEEDED'))).toBe(false);
    expect(isActive(run('FAILED'))).toBe(false);
    expect(isActive(undefined)).toBe(false);
  });
});

describe('stepStatus', () => {
  it('maps step statuses', () => {
    expect(stepStatus('pending')).toBe('pending');
    expect(stepStatus('in-progress')).toBe('in-progress');
    expect(stepStatus('success')).toBe('success');
    expect(stepStatus('error')).toBe('error');
    expect(stepStatus('weird')).toBe('pending');
  });
});

describe('accountOutcome', () => {
  it('maps each account outcome to a status', () => {
    expect(accountOutcome('scanned')).toBe('success');
    expect(accountOutcome('scanning')).toBe('in-progress');
    expect(accountOutcome('partial: backup AccessDenied')).toBe('warning');
    expect(accountOutcome('not scanned: AssumeRole AccessDenied')).toBe('error');
    expect(accountOutcome('denied: S3 bucket list')).toBe('error');
    expect(accountOutcome('something else')).toBe('pending');
  });
});
