const PREFIX = 's3dp-exclude:';

/** Comma-separated patterns, trimmed and lower-cased. */
export function parsePatterns(text: string): string[] {
  return text
    .split(',')
    .map((p) => p.trim().toLowerCase())
    .filter(Boolean);
}

// Linear glob match (no regular expressions, so no catastrophic backtracking).
function matches(name: string, pattern: string): boolean {
  if (!pattern.includes('*')) return name.includes(pattern);
  const parts = pattern.split('*');
  const first = parts[0];
  const last = parts[parts.length - 1];
  if (!name.startsWith(first) || !name.endsWith(last)) return false;
  let pos = first.length;
  const end = name.length - last.length;
  if (end < pos) return false;
  for (const part of parts.slice(1, -1)) {
    if (!part) continue;
    const idx = name.indexOf(part, pos);
    if (idx < 0 || idx + part.length > end) return false;
    pos = idx + part.length;
  }
  return true;
}

/** `*` is a wildcard over the whole name; a term without `*` matches when contained. */
export function isExcluded(bucket: string, patterns: string[]): boolean {
  const name = bucket.toLowerCase();
  return patterns.some((p) => matches(name, p));
}

/** reportKey is the run id, or "sample". */
export function loadExclude(reportKey: string): string {
  try {
    return localStorage.getItem(PREFIX + reportKey) ?? '';
  } catch {
    return '';
  }
}

export function saveExclude(reportKey: string, text: string): void {
  try {
    localStorage.setItem(PREFIX + reportKey, text);
  } catch {
    // Storage unavailable: the filter still works for this session.
  }
}
