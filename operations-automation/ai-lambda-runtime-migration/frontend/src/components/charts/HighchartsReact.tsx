// A thin React wrapper around Highcharts (no highcharts-react-official dep).
//
// Cloudscape v3 ships no cartesian-chart component (checked against the
// installed @cloudscape-design/components 3.0.1401 — only area/bar/line/
// mixed-line-bar/pie exist), so the bubble and scatter charts, which the
// legacy Cloudscape charts cannot draw, are rendered with Highcharts directly.
// This wrapper creates the chart on mount, updates it on option changes, and
// destroys it on unmount. It imports Highcharts from ./highcharts-setup — the
// single instance onto which highcharts-more (bubble) and accessibility have
// already self-registered — so every chart shares one registration.
import { useEffect, useRef } from 'react';
import Highcharts from './highcharts-setup';

interface HighchartsReactProps {
  options: Highcharts.Options;
  // A label used only for the fallback container role/aria when a chart is empty.
  ariaLabel?: string;
}

export default function HighchartsReact({ options, ariaLabel }: HighchartsReactProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Highcharts.Chart | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    chartRef.current = Highcharts.chart(containerRef.current, options);
    return () => {
      chartRef.current?.destroy();
      chartRef.current = null;
    };
    // Recreate the chart whenever the options object identity changes. Charts
    // here get a fresh options object only when their source data changes, so
    // recreation (rather than update) keeps the wrapper simple and correct.
  }, [options]);

  return <div ref={containerRef} role="img" aria-label={ariaLabel} style={{ width: '100%', height: 320 }} />;
}
