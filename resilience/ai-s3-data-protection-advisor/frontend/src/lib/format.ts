import type { RiskKey } from './types';

const pad = (n: number) => String(n).padStart(2, '0');

/** `YYYY-MM-DD HH:MM UTC`; empty for a missing or invalid value. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

/** `42s`, `7m 5s`, `1h 2m`; empty while a run is unfinished or on bad input. */
export function formatDuration(start: string | null | undefined, end: string | null | undefined): string {
  if (!start || !end) return '';
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '';
  const total = Math.round(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

const RISKS: Record<RiskKey, string> = {
  he: 'Human error',
  ma: 'Malicious activity',
  rd: 'Regional disruption',
};

export function riskName(k: RiskKey): string {
  return RISKS[k];
}

/** "name (id)", or the id once when the name is missing or equals the id. */
export function accountLabel(name: string | null | undefined, id: string | null | undefined): string {
  const n = (name ?? '').trim();
  const i = (id ?? '').trim();
  if (!n) return i;
  if (!i || n === i) return n;
  return `${n} (${i})`;
}
