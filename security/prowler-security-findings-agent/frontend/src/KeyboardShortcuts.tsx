import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

/**
 * Global keyboard shortcuts handler.
 *
 * - `g` then `d` / `f` / `c` / `i` / `$` → navigate to Dashboard / Findings /
 *   Compliance / Investigations / Cost. Modelled after GitHub.
 * - `?` → open the shortcuts help overlay.
 * - `Escape` (when the overlay is open) → close it.
 *
 * Keys are ignored while the user is typing in an input, textarea, or
 * contentEditable element so normal form entry still works.
 */

type RouteKey = 'd' | 'f' | 'c' | 'i' | '$';
const ROUTE: Record<RouteKey, string> = {
  d: '/',
  f: '/findings',
  c: '/compliance',
  i: '/investigations',
  $: '/cost',
};

function isTyping(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (el.isContentEditable) return true;
  return false;
}

export default function KeyboardShortcuts() {
  const navigate = useNavigate();
  const [helpOpen, setHelpOpen] = useState(false);
  const [gPending, setGPending] = useState(false);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === '?') {
        if (isTyping(e)) return;
        e.preventDefault();
        setHelpOpen((v) => !v);
        return;
      }
      if (e.key === 'Escape' && helpOpen) {
        e.preventDefault();
        setHelpOpen(false);
        return;
      }
      if (isTyping(e)) return;
      if (e.key === 'g') {
        e.preventDefault();
        setGPending(true);
        // Reset the chord after 1s if nothing follows.
        setTimeout(() => setGPending(false), 1000);
        return;
      }
      if (gPending) {
        const k = e.key as RouteKey;
        if (ROUTE[k]) {
          e.preventDefault();
          navigate(ROUTE[k]);
        }
        setGPending(false);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [navigate, helpOpen, gPending]);

  if (!helpOpen) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
      onClick={() => setHelpOpen(false)}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.55)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9999,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--soc-bg-elev)',
          border: '1px solid var(--soc-border)',
          borderRadius: 10,
          padding: '20px 26px',
          minWidth: 360,
          maxWidth: 520,
          color: 'var(--soc-fg)',
          boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
        }}
      >
        <h2 style={{ marginTop: 0, marginBottom: 14, fontSize: 18 }}>Keyboard shortcuts</h2>
        <dl style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: 18, rowGap: 8, margin: 0, fontSize: 13 }}>
          <Shortcut keys={['⌘', 'K']} desc="Command palette (search everything)" />
          <Shortcut keys={['/']} desc="Command palette (alt)" />
          <Shortcut keys={['g', 'd']} desc="Go to Dashboard" />
          <Shortcut keys={['g', 'f']} desc="Go to Findings" />
          <Shortcut keys={['g', 'c']} desc="Go to Compliance" />
          <Shortcut keys={['g', 'i']} desc="Go to Investigations" />
          <Shortcut keys={['g', '$']} desc="Go to Cost" />
          <Shortcut keys={['Esc']} desc="Back / close dialog" />
          <Shortcut keys={['?']} desc="Show / hide this help" />
        </dl>
        <div style={{ marginTop: 14, color: 'var(--soc-fg-dim)', fontSize: 11 }}>
          Click outside or press <Kbd>Esc</Kbd> to close.
        </div>
      </div>
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd
      style={{
        display: 'inline-block',
        padding: '1px 6px',
        border: '1px solid var(--soc-border-strong)',
        borderRadius: 4,
        background: 'var(--soc-bg-elev-2)',
        fontSize: 11,
        fontFamily: 'JetBrains Mono, monospace',
        color: 'var(--soc-fg)',
        minWidth: 18,
        textAlign: 'center',
      }}
    >
      {children}
    </kbd>
  );
}

function Shortcut({ keys, desc }: { keys: string[]; desc: string }) {
  return (
    <>
      <dt style={{ display: 'flex', gap: 4, alignItems: 'center', justifyContent: 'flex-end' }}>
        {keys.map((k, i) => (
          <span key={i} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            {i > 0 && <span style={{ color: 'var(--soc-fg-dim)', fontSize: 11 }}>then</span>}
            <Kbd>{k}</Kbd>
          </span>
        ))}
      </dt>
      <dd style={{ margin: 0, color: 'var(--soc-fg-muted)' }}>{desc}</dd>
    </>
  );
}
