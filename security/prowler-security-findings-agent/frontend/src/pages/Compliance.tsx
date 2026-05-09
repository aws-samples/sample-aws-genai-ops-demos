import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  ContentLayout,
  Container,
  Header,
  SpaceBetween,
} from '@cloudscape-design/components';
import { Finding, listFindings } from '../api';
import { COLOR } from '../theme';

import { FRAMEWORKS, matchesFramework } from '../frameworks';

/** Pure-SVG radial gauge. No third-party charting library. */
function Ring({ pct, size = 160 }: { pct: number; size?: number }) {
  const color = pct >= 80 ? 'var(--soc-ok)' : pct >= 50 ? 'var(--soc-medium)' : 'var(--soc-critical)';
  const stroke = 14;
  const r = (size - stroke) / 2;
  const cx = size / 2;
  const cy = size / 2;
  // Semicircle arc: from 180° → 0°, length = π·r. Offset the dasharray by
  // (1 - pct/100) to render the "passing" fraction from left.
  const circumference = Math.PI * r;
  const dashOffset = circumference * (1 - pct / 100);
  return (
    <svg
      width={size}
      height={size * 0.68}
      viewBox={`0 0 ${size} ${size * 0.68}`}
      role="img"
      aria-label={`${pct} percent compliant`}
    >
      <title>{`${pct}% compliant`}</title>
      {/* background arc */}
      <path
        d={`M ${stroke / 2} ${cy} A ${r} ${r} 0 0 1 ${size - stroke / 2} ${cy}`}
        fill="none"
        stroke="var(--soc-border)"
        strokeWidth={stroke}
        strokeLinecap="round"
      />
      {/* foreground arc */}
      <path
        d={`M ${stroke / 2} ${cy} A ${r} ${r} 0 0 1 ${size - stroke / 2} ${cy}`}
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={dashOffset}
        className="soc-chart-anim"
        style={{ transition: 'stroke-dashoffset 0.6s ease' }}
      />
      <text
        x="50%"
        y={cy - 2}
        textAnchor="middle"
        fontFamily="Inter, -apple-system, sans-serif"
        fontSize={size * 0.22}
        fontWeight={700}
        fill={COLOR.fg}
      >
        {pct}%
      </text>
    </svg>
  );
}

export default function Compliance() {
  const navigate = useNavigate();
  const [items, setItems] = useState<Finding[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await listFindings({ limit: 500 });
      setItems(r.items || []);
    } catch (e: any) {
      setError(e?.message || 'Failed to load findings');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  const frameworkStats = useMemo(() => {
    return FRAMEWORKS.map((fw) => {
      const matches = items.filter((f) => matchesFramework(f, fw.match));
      const total = matches.length;
      const failing = matches.filter((m) => m.status === 'FAIL').length;
      const passing = total - failing;
      const pct = total === 0 ? 0 : Math.round((passing / total) * 100);
      return { ...fw, total, failing, passing, pct };
    });
  }, [items]);

  const overallTotal = items.length;
  const overallPassing = items.filter((i) => i.status === 'PASS').length;
  const overallPct = overallTotal === 0 ? 0 : Math.round((overallPassing / overallTotal) * 100);
  const activeFrameworks = frameworkStats.filter((f) => f.total > 0).length;

  return (
    <ContentLayout
      header={
        <div className="soc-hero">
          <h1>Compliance</h1>
          <div className="soc-hero-sub">Your AWS account mapped against industry security frameworks, computed live from the Prowler findings.</div>
          <div className="soc-compliance-hero">
            <div style={{ textAlign: 'center' }}>
              <Ring pct={overallPct} size={220} />
              <div style={{ color: COLOR.fgMuted, fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.1em', marginTop: 6 }}>
                Overall posture
              </div>
            </div>
            <div className="soc-compliance-kpis">
              <div className="soc-kpi">
                <div className="soc-kpi-label">Checks</div>
                <div className="soc-kpi-value">{overallTotal}</div>
              </div>
              <div className="soc-kpi soc-kpi--ok">
                <div className="soc-kpi-label">Passing</div>
                <div className="soc-kpi-value">{overallPassing}</div>
              </div>
              <div className="soc-kpi soc-kpi--critical">
                <div className="soc-kpi-label">Failing</div>
                <div className="soc-kpi-value">{overallTotal - overallPassing}</div>
              </div>
              <div className="soc-kpi soc-kpi--accent">
                <div className="soc-kpi-label">Frameworks</div>
                <div className="soc-kpi-value">{activeFrameworks}</div>
              </div>
            </div>
          </div>
        </div>
      }
    >
      <SpaceBetween size="l">
        {error && (
          <Alert type="error" dismissible onDismiss={() => setError(null)} action={<Button onClick={load}>Retry</Button>}>
            {error}
          </Alert>
        )}
        {loading && <Box color="text-status-inactive">Loading compliance data…</Box>}

        <Container header={<Header variant="h2" description="Pass rate per framework. Click a card to open all findings for that framework.">Framework compliance</Header>}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 16 }}>
            {frameworkStats.map((fw) => (
              <button
                key={fw.key}
                className="soc-tile"
                type="button"
                aria-disabled={fw.total === 0}
                aria-label={fw.total > 0
                  ? `${fw.label}: ${fw.pct}% passing (${fw.passing} of ${fw.total})`
                  : `${fw.label}: no checks mapped`}
                onClick={() => fw.total > 0 && navigate(`/findings?framework=${encodeURIComponent(fw.key)}`)}
                style={{
                  flexDirection: 'column',
                  gap: 6,
                  padding: 16,
                  opacity: fw.total === 0 ? 0.4 : 1,
                  cursor: fw.total === 0 ? 'default' : 'pointer',
                  alignItems: 'stretch',
                }}
              >
                <div style={{ color: COLOR.fg, fontWeight: 700, fontSize: 14 }}>{fw.label}</div>
                <div style={{ color: COLOR.fgDim, fontSize: 11, lineHeight: 1.3 }}>{fw.description}</div>
                <div style={{ width: '100%', marginTop: 4, textAlign: 'center' }}>
                  {fw.total > 0 ? (
                    <Ring pct={fw.pct} size={160} />
                  ) : (
                    <Box color="text-status-inactive" textAlign="center" padding={{ vertical: 'l' }}>
                      <div style={{ fontSize: 11 }}>no checks mapped</div>
                    </Box>
                  )}
                </div>
                {fw.total > 0 && (
                  <div style={{ color: COLOR.fgMuted, fontSize: 11 }}>
                    {fw.passing}/{fw.total} passing · <span style={{ color: COLOR.critical }}>{fw.failing} failing</span>
                  </div>
                )}
              </button>
            ))}
          </div>
        </Container>
      </SpaceBetween>
    </ContentLayout>
  );
}
