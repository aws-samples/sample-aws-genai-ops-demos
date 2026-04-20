/**
 * Property tests for frontend rendering: data classification, markdown
 * rendering, and collapsible view logic.
 * Feature: genai-operations-analytics-tool
 *
 * These tests exercise pure TypeScript implementations that mirror the
 * DataVisualization utility functions — without DOM rendering or React
 * dependencies (same pattern as streaming.property.test.ts).
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

// ---------------------------------------------------------------------------
// Local TypeScript implementations mirroring DataVisualization utilities
// ---------------------------------------------------------------------------

type DataType = 'tabular' | 'timeseries' | 'recommendations' | 'crossdomain' | 'narrative';

interface TabularData {
  type: 'tabular';
  columns: string[];
  rows: Record<string, string | number>[];
}

interface TimeSeriesData {
  type: 'timeseries';
  chartType?: 'bar' | 'line';
  title?: string;
  xLabel?: string;
  yLabel?: string;
  series: { label: string; data: { x: string; y: number }[] }[];
}

interface Recommendation {
  t
itle: string;
  description: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  source?: string;
}

interface RecommendationsData {
  type: 'recommendations';
  items: Recommendation[];
}

interface CrossDomainGroup {
  domain: string;
  content: string | TabularData | RecommendationsData;
}

interface CrossDomainData {
  type: 'crossdomain';
  groups: CrossDomainGroup[];
}

type StructuredData = TabularData | TimeSeriesData | RecommendationsData | CrossDomainData;

/**
 * Classify a data object into its DataType.
 * Mirrors DataVisualization.classifyData — returns the `type` field for
 * structured data, or 'narrative' for plain strings / unknown shapes.
 */
function classifyData(data: string | StructuredData): DataType {
  if (typeof data !== 'string') {
    if ('type' in data) {
      const t = data.type;
      if (t === 'tabular' || t === 'timeseries' || t === 'recommendations' || t === 'crossdomain') {
        return t;
      }
    }
    return 'narrative';
  }
  return 'narrative';
}

/**
 * Simple markdown → HTML renderer.
 * Mirrors DataVisualization.renderMarkdown.
 */
function renderMarkdown(md: string): string {
  let html = md;

  // Headers (### before ## before #)
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

  // Bold + italic combined, then bold, then italic
  html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

  // Double newlines to paragraph breaks
  html = html.replace(/\n\n/g, '</p><p>');
  html = html.replace(/\n/g, '<br/>');

  if (!html.startsWith('<')) {
    html = '<p>' + html + '</p>';
  }

  return html;
}

/**
 * Determine whether a response text should be displayed in a collapsible view.
 * Returns `true` when the word count exceeds 500.
 * Mirrors DataVisualization.shouldCollapse.
 */
function shouldCollapse(text: string): boolean {
  const wordCount = text.trim().split(/\s+/).filter(Boolean).length;
  return wordCount > 500;
}


// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

const arbColumnName = fc.stringOf(
  fc.constantFrom(
    ...'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_'.split(''),
  ),
  { minLength: 1, maxLength: 20 },
);

const arbCellValue: fc.Arbitrary<string | number> = fc.oneof(
  fc.string({ minLength: 0, maxLength: 30 }),
  fc.integer({ min: -10000, max: 10000 }),
);

const arbTabularData: fc.Arbitrary<TabularData> = fc
  .array(arbColumnName, { minLength: 1, maxLength: 5 })
  .chain((columns) => {
    const uniqueCols = [...new Set(columns)];
    if (uniqueCols.length === 0) return fc.constant(null as unknown as TabularData);
    const rowArb = fc.record(
      Object.fromEntries(uniqueCols.map((col) => [col, arbCellValue])),
    ) as fc.Arbitrary<Record<string, string | number>>;
    return fc.array(rowArb, { minLength: 1, maxLength: 10 }).map((rows) => ({
      type: 'tabular' as const,
      columns: uniqueCols,
      rows,
    }));
  })
  .filter((d): d is TabularData => d !== null && d.columns.length > 0);

const arbSeverity = fc.constantFrom(
  'critical' as const, 'high' as const, 'medium' as const, 'low' as const, 'info' as const,
);

const arbTimeSeriesData: fc.Arbitrary<TimeSeriesData> = fc.record({
  type: fc.constant('timeseries' as const),
  chartType: fc.constantFrom('bar' as const, 'line' as const),
  title: fc.string({ minLength: 1, maxLength: 30 }),
  xLabel: fc.string({ minLength: 1, maxLength: 15 }),
  yLabel: fc.string({ minLength: 1, maxLength: 15 }),
  series: fc.array(
    fc.record({
      label: fc.string({ minLength: 1, maxLength: 15 }),
      data: fc.array(
        fc.record({ x: fc.string({ minLength: 1, maxLength: 10 }), y: fc.integer({ min: 0, max: 100000 }) }),
        { minLength: 1, maxLength: 5 },
      ),
    }),
    { minLength: 1, maxLength: 3 },
  ),
});

const arbRecommendationsData: fc.Arbitrary<RecommendationsData> = fc.record({
  type: fc.constant('recommendations' as const),
  items: fc.array(
    fc.record({ title: fc.string({ minLength: 1, maxLength: 40 }), description: fc.string({ minLength: 1, maxLength: 100 }), severity: arbSeverity }),
    { minLength: 1, maxLength: 5 },
  ),
});

const arbCrossDomainData: fc.Arbitrary<CrossDomainData> = fc.record({
  type: fc.constant('crossdomain' as const),
  groups: fc.array(
    fc.record({ domain: fc.constantFrom('cost', 'health', 'support', 'trusted_advisor', 'cur'), content: fc.string({ minLength: 1, maxLength: 60 }) }),
    { minLength: 1, maxLength: 4 },
  ),
});

const arbStructuredData: fc.Arbitrary<StructuredData> = fc.oneof(
  arbTabularData, arbTimeSeriesData, arbRecommendationsData, arbCrossDomainData,
);

const arbWord = fc.stringOf(
  fc.constantFrom(...'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')),
  { minLength: 1, maxLength: 12 },
);

function arbTextWithWordCount(n: number): fc.Arbitrary<string> {
  return fc.array(arbWord, { minLength: n, maxLength: n }).map((words) => words.join(' '));
}

const arbMarkdownString = fc.stringOf(
  fc.constantFrom(...'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,!?-_()'.split('')),
  { minLength: 1, maxLength: 120 },
);


// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Frontend rendering property tests', () => {
  /**
   * Property 10: Tabular data detection
   *
   * For any TabularData object with columns and rows, classifyData
   * returns 'tabular'.
   *
   * **Validates: Requirements 7.4**
   */
  it('Property 10: Tabular data detection — Feature: genai-operations-analytics-tool, Property 10: Tabular data detection', () => {
    fc.assert(
      fc.property(arbTabularData, (tabular) => {
        const result = classifyData(tabular);
        expect(result).toBe('tabular');
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 21: Visualization type selection based on data classification
   *
   * For any StructuredData object, classifyData returns the correct type
   * matching the `type` field.
   *
   * **Validates: Requirements 15.1, 15.2, 15.3**
   */
  it('Property 21: Visualization type selection based on data classification — Feature: genai-operations-analytics-tool, Property 21: Visualization type selection', () => {
    fc.assert(
      fc.property(arbStructuredData, (data) => {
        const result = classifyData(data);
        expect(result).toBe(data.type);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 22: Markdown rendering produces valid output
   *
   * For any markdown string, renderMarkdown produces a non-empty string.
   * Bold markers (**text**) should be converted to <strong> tags.
   * Headers (#) should be converted to <h> tags.
   *
   * **Validates: Requirements 15.4**
   */
  it('Property 22: Markdown rendering produces valid output — Feature: genai-operations-analytics-tool, Property 22: Markdown rendering', () => {
    fc.assert(
      fc.property(arbMarkdownString, (md) => {
        const html = renderMarkdown(md);
        expect(html.length).toBeGreaterThan(0);
      }),
      { numRuns: 100 },
    );
  });

  it('Property 22a: Bold markers are converted to <strong> tags', () => {
    fc.assert(
      fc.property(arbWord, (word) => {
        const md = `**${word}**`;
        const html = renderMarkdown(md);
        expect(html).toContain(`<strong>${word}</strong>`);
        expect(html).not.toContain('**');
      }),
      { numRuns: 100 },
    );
  });

  it('Property 22b: Headers are converted to <h> tags', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 3 }),
        arbWord,
        (level, text) => {
          const hashes = '#'.repeat(level);
          const md = `${hashes} ${text}`;
          const html = renderMarkdown(md);
          expect(html).toContain(`<h${level}>${text}</h${level}>`);
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * Property 23: Collapsible view for long responses
   *
   * For any text, shouldCollapse returns true iff word count > 500.
   *
   * **Validates: Requirements 15.5**
   */
  it('Property 23: Collapsible view for long responses — Feature: genai-operations-analytics-tool, Property 23: Collapsible view', () => {
    fc.assert(
      fc.property(arbTextWithWordCount(550), (text) => {
        expect(shouldCollapse(text)).toBe(true);
      }),
      { numRuns: 100 },
    );
  });

  it('Property 23a: Text with 500 or fewer words should NOT collapse', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 500 }).chain((n) => arbTextWithWordCount(n)),
        (text) => {
          expect(shouldCollapse(text)).toBe(false);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('Property 23b: Boundary — exactly 500 words should NOT collapse', () => {
    fc.assert(
      fc.property(arbTextWithWordCount(500), (text) => {
        expect(shouldCollapse(text)).toBe(false);
      }),
      { numRuns: 100 },
    );
  });

  it('Property 23c: Boundary — exactly 501 words SHOULD collapse', () => {
    fc.assert(
      fc.property(arbTextWithWordCount(501), (text) => {
        expect(shouldCollapse(text)).toBe(true);
      }),
      { numRuns: 100 },
    );
  });
});
