import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Finding, listFindings, runScan } from './api';

/**
 * Global ⌘K / Ctrl+K / "/" command palette.
 *
 * Categories:
 *   - Pages:     instant navigation to Dashboard / Findings / Compliance / …
 *   - Actions:   demo-level operations (run scan, etc.)
 *   - Findings:  fuzzy match across the current account's findings
 *                (check_title + check_id + resource_uid + service)
 *   - Services:  jump to Findings filtered by service
 *
 * Findings are loaded lazily on first open and cached for 60 s so subsequent
 * openings are instant.
 */

interface Command {
  id: string;
  group: 'Pages' | 'Actions' | 'Findings' | 'Services';
  title: string;
  subtitle?: string;
  // Optional computed score; higher is better. 0 hides the command from
  // non-empty queries.
  score?: number;
  run: () => void | Promise<void>;
}

const PAGE_COMMANDS = [
  { id: 'go-dashboard', title: 'Go to Dashboard', path: '/' },
  { id: 'go-findings', title: 'Go to Findings', path: '/findings' },
  { id: 'go-compliance', title: 'Go to Compliance', path: '/compliance' },
  { id: 'go-investigations', title: 'Go to Investigations', path: '/investigations' },
  { id: 'go-cost', title: 'Go to Cost & GenAI telemetry', path: '/cost' },
] as const;

function fuzzyScore(haystack: string, needle: string): number {
  if (!needle) return 1;
  const h = haystack.toLowerCase();
  const n = needle.toLowerCase();
  if (h === n) return 100;
  if (h.startsWith(n)) return 80;
  if (h.includes(n)) return 50;
  // Cheap subsequence match — every needle char present in order gets 20.
  let i = 0;
  for (const ch of h) {
    if (ch === n[i]) i++;
    if (i === n.length) return 20;
  }
  return 0;
}

/**
 * Cached findings list — refreshed at most once per minute to keep palette
 * responsive without blasting the API on every keystroke.
 */
let _findingsCache: { ts: number; items: Finding[] } | null = null;

async function loadFindingsCached(): Promise<Finding[]> {
  if (_findingsCache && Date.now() - _findingsCache.ts < 60_000) {
    return _findingsCache.items;
  }
  try {
    const r = await listFindings({ limit: 500 });
    _findingsCache = { ts: Date.now(), items: r.items || [] };
    return _findingsCache.items;
  } catch {
    return _findingsCache?.items || [];
  }
}

export default function CommandPalette() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [findings, setFindings] = useState<Finding[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);

  // Open handlers: ⌘K / Ctrl+K / "/"
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((v) => !v);
        return;
      }
      if (!open && e.key === '/' && !typing) {
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open]);

  // Prefetch findings when opening
  useEffect(() => {
    if (!open) return;
    setQ('');
    setActiveIdx(0);
    loadFindingsCached().then(setFindings);
    // Focus the input on the next paint (after the modal mounts)
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

  // Build the command list filtered by the current query
  const commands = useMemo<Command[]>(() => {
    const needle = q.trim();

    // Pages
    const pageCmds: Command[] = PAGE_COMMANDS.map((p) => ({
      id: p.id,
      group: 'Pages',
      title: p.title,
      score: fuzzyScore(p.title, needle),
      run: () => navigate(p.path),
    }));

    // Actions
    const actionCmds: Command[] = [
      {
        id: 'run-scan',
        group: 'Actions',
        title: 'Run Prowler scan now',
        subtitle: 'Starts a Fargate task',
        score: fuzzyScore('run prowler scan now', needle),
        run: async () => {
          try { await runScan(); navigate('/'); } catch { /* noop */ }
        },
      },
    ];

    // Findings (top 10 by score)
    const findingCmds: Command[] = findings
      .map((f) => {
        const label = f.check_title || f.check_id;
        const hay = `${label} ${f.check_id} ${f.resource_uid} ${f.service_name} ${f.severity} ${f.status}`;
        return {
          id: `finding-${f.finding_uid}`,
          group: 'Findings' as const,
          title: label,
          subtitle: `${f.severity} · ${f.status} · ${f.resource_uid}`,
          score: needle ? fuzzyScore(hay, needle) : (f.status === 'FAIL' ? 1 : 0),
          run: () => navigate(`/findings/${encodeURIComponent(f.finding_uid)}`),
        };
      })
      .filter((c) => (c.score || 0) > 0)
      .sort((a, b) => (b.score || 0) - (a.score || 0))
      .slice(0, 10);

    // Services
    const services = new Set(findings.map((f) => f.service_name).filter(Boolean));
    const serviceCmds: Command[] = Array.from(services)
      .map((s) => ({
        id: `service-${s}`,
        group: 'Services' as const,
        title: `View failing findings in ${s}`,
        subtitle: s,
        score: fuzzyScore(s, needle),
        run: () => navigate(`/findings?service=${encodeURIComponent(s)}&status=FAIL`),
      }))
      .filter((c) => (c.score || 0) > 0)
      .sort((a, b) => (b.score || 0) - (a.score || 0))
      .slice(0, 6);

    // Empty query → show Pages + Actions + top 5 failing findings + top 5 services
    if (!needle) {
      const topFailing = findingCmds.slice(0, 5);
      const topServices = serviceCmds.slice(0, 5);
      return [...pageCmds, ...actionCmds, ...topFailing, ...topServices];
    }

    return [...pageCmds, ...actionCmds, ...findingCmds, ...serviceCmds]
      .filter((c) => (c.score || 0) > 0)
      .sort((a, b) => (b.score || 0) - (a.score || 0));
  }, [q, findings, navigate]);

  // Reset active index when list shrinks under current selection
  useEffect(() => {
    if (activeIdx >= commands.length) setActiveIdx(Math.max(0, commands.length - 1));
  }, [commands.length, activeIdx]);

  // Scroll the active row into view
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLLIElement>(`[data-idx="${activeIdx}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [activeIdx, open]);

  const runCommand = useCallback(
    async (cmd: Command) => {
      setOpen(false);
      await cmd.run();
    },
    [],
  );

  if (!open) return null;

  // Group labels rendered inline so users see section breaks.
  const items: Array<{ kind: 'header'; title: string } | { kind: 'cmd'; cmd: Command; idx: number }> = [];
  let lastGroup: string | null = null;
  let idx = 0;
  for (const c of commands) {
    if (c.group !== lastGroup) {
      items.push({ kind: 'header', title: c.group });
      lastGroup = c.group;
    }
    items.push({ kind: 'cmd', cmd: c, idx });
    idx++;
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      onClick={() => setOpen(false)}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.55)',
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        zIndex: 9999,
        paddingTop: '10vh',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--soc-bg-elev)',
          border: '1px solid var(--soc-border)',
          borderRadius: 12,
          width: 'min(640px, 92vw)',
          maxHeight: '70vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 30px 80px rgba(0,0,0,0.55)',
          overflow: 'hidden',
          color: 'var(--soc-fg)',
        }}
      >
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => { setQ(e.target.value); setActiveIdx(0); }}
          placeholder="Search findings, services, pages…"
          spellCheck={false}
          autoComplete="off"
          aria-label="Command palette search"
          aria-autocomplete="list"
          onKeyDown={(e) => {
            if (e.key === 'Escape') { setOpen(false); return; }
            if (e.key === 'ArrowDown') { e.preventDefault(); setActiveIdx((i) => Math.min(i + 1, commands.length - 1)); return; }
            if (e.key === 'ArrowUp') { e.preventDefault(); setActiveIdx((i) => Math.max(i - 1, 0)); return; }
            if (e.key === 'Enter') {
              e.preventDefault();
              const c = commands[activeIdx];
              if (c) runCommand(c);
            }
          }}
          style={{
            width: '100%',
            padding: '16px 20px',
            fontSize: 16,
            background: 'transparent',
            border: 0,
            borderBottom: '1px solid var(--soc-border)',
            color: 'var(--soc-fg)',
            outline: 'none',
            boxSizing: 'border-box',
          }}
        />
        {commands.length === 0 ? (
          <div style={{ padding: 24, color: 'var(--soc-fg-dim)', fontSize: 13 }}>
            No matches. Try "critical", a service name, or part of a check ID.
          </div>
        ) : (
          <ul
            ref={listRef}
            role="listbox"
            aria-label="Command palette results"
            style={{ margin: 0, padding: '6px 0', listStyle: 'none', overflowY: 'auto', flex: 1 }}
          >
            {items.map((it, i) =>
              it.kind === 'header' ? (
                <li
                  key={`h-${it.title}-${i}`}
                  aria-hidden="true"
                  style={{
                    padding: '8px 18px 4px',
                    fontSize: 10,
                    textTransform: 'uppercase',
                    letterSpacing: '0.08em',
                    color: 'var(--soc-fg-dim)',
                  }}
                >
                  {it.title}
                </li>
              ) : (
                <li
                  key={it.cmd.id}
                  role="option"
                  data-idx={it.idx}
                  aria-selected={activeIdx === it.idx}
                  onMouseEnter={() => setActiveIdx(it.idx)}
                  onClick={() => runCommand(it.cmd)}
                  style={{
                    padding: '8px 18px',
                    cursor: 'pointer',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 2,
                    background: activeIdx === it.idx ? 'rgba(79,143,255,0.10)' : 'transparent',
                    borderLeft: `3px solid ${activeIdx === it.idx ? 'var(--soc-accent)' : 'transparent'}`,
                  }}
                >
                  <span style={{ color: 'var(--soc-fg)', fontWeight: 500, fontSize: 13 }}>
                    {it.cmd.title}
                  </span>
                  {it.cmd.subtitle && (
                    <span style={{ color: 'var(--soc-fg-dim)', fontSize: 11, fontFamily: it.cmd.group === 'Findings' ? 'JetBrains Mono, monospace' : undefined }}>
                      {it.cmd.subtitle}
                    </span>
                  )}
                </li>
              ),
            )}
          </ul>
        )}
        <div
          style={{
            padding: '8px 18px',
            borderTop: '1px solid var(--soc-border)',
            fontSize: 11,
            color: 'var(--soc-fg-dim)',
            display: 'flex',
            gap: 16,
            flexWrap: 'wrap',
          }}
        >
          <span>
            <kbd style={kbdStyle}>↑</kbd> <kbd style={kbdStyle}>↓</kbd> navigate
          </span>
          <span>
            <kbd style={kbdStyle}>↵</kbd> select
          </span>
          <span>
            <kbd style={kbdStyle}>Esc</kbd> close
          </span>
          <span style={{ marginLeft: 'auto' }}>
            Open with <kbd style={kbdStyle}>⌘</kbd><kbd style={kbdStyle}>K</kbd> or <kbd style={kbdStyle}>/</kbd>
          </span>
        </div>
      </div>
    </div>
  );
}

const kbdStyle: React.CSSProperties = {
  display: 'inline-block',
  padding: '0 5px',
  border: '1px solid var(--soc-border-strong)',
  borderRadius: 3,
  background: 'var(--soc-bg-elev-2)',
  fontSize: 10,
  fontFamily: 'JetBrains Mono, monospace',
  color: 'var(--soc-fg)',
  minWidth: 14,
  textAlign: 'center' as const,
};
