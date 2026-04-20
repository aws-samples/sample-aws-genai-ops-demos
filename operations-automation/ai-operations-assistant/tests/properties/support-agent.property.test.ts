/**
 * Property tests for Support Agent formatting logic.
 * Feature: genai-operations-analytics-tool
 *
 * These tests exercise a pure TypeScript implementation that mirrors the
 * Python Support Agent's `_format_support_cases` behaviour so it can be
 * verified with fast-check without calling boto3.
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { arbSupportCase } from '../generators/support-case.gen';

// ---------------------------------------------------------------------------
// Local TypeScript implementation mirroring the Python Support Agent logic
// ---------------------------------------------------------------------------

/** A support case object matching the shape produced by the generator. */
interface SupportCase {
  caseId: string;
  displayId: string;
  subject: string;
  status: string;
  serviceCode: string;
  severityCode: string;
  categoryCode: string;
  timeCreated: string;
  recentCommunications: {
    communications: Array<{
      body: string;
      submittedBy: string;
      timeCreated: string;
    }>;
  };
  language: string;
}

/**
 * Format support cases into a human-readable response string.
 * Mirrors `_format_support_cases` in `agents/support-agent/main.py`.
 *
 * The output MUST contain for each case:
 *  - case ID (displayId or caseId)
 *  - subject
 *  - status
 *  - severity (severityCode)
 *  - creation date (timeCreated)
 */
function formatSupportCases(cases: SupportCase[]): string {
  if (cases.length === 0) {
    return 'No support cases found matching the specified criteria.';
  }

  const lines: string[] = [`AWS Support Cases (${cases.length} found)`, ''];
  for (const c of cases) {
    const caseId = c.displayId || c.caseId || 'N/A';
    const subject = c.subject ?? 'N/A';
    const status = c.status ?? 'N/A';
    const severity = c.severityCode ?? 'N/A';
    const created = c.timeCreated ?? 'N/A';
    const service = c.serviceCode ?? 'N/A';

    lines.push(
      `  Case: ${caseId} | Subject: ${subject} | ` +
        `Status: ${status} | Severity: ${severity} | ` +
        `Created: ${created} | Service: ${service}`,
    );
  }

  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Support Agent property tests', () => {
  /**
   * Property 7: Support case formatting includes required fields
   *
   * For any support case object, the Support Agent's response formatter
   * should produce output containing the case ID, subject, status,
   * severity, and creation date.
   *
   * **Validates: Requirements 4.3**
   */
  it('Property 7: Support case formatting includes required fields — Feature: genai-operations-analytics-tool, Property 7: Support case formatting includes required fields', () => {
    fc.assert(
      fc.property(
        fc.array(arbSupportCase, { minLength: 1, maxLength: 10 }),
        (cases) => {
          const output = formatSupportCases(cases);

          // Header must include the case count
          expect(output).toContain(`${cases.length} found`);

          // Each case's 5 required fields must appear in the output
          for (const c of cases) {
            const expectedId = c.displayId || c.caseId;
            expect(output).toContain(`Case: ${expectedId}`);
            expect(output).toContain(`Subject: ${c.subject}`);
            expect(output).toContain(`Status: ${c.status}`);
            expect(output).toContain(`Severity: ${c.severityCode}`);
            expect(output).toContain(`Created: ${c.timeCreated}`);
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * Supplementary check: empty cases list returns the no-cases message.
   * This validates Requirement 4.4 edge case handling.
   */
  it('Property 7 (empty case): empty cases list returns no-cases message — Feature: genai-operations-analytics-tool, Property 7: Support case formatting includes required fields', () => {
    const output = formatSupportCases([]);
    expect(output).toBe(
      'No support cases found matching the specified criteria.',
    );
  });
});
