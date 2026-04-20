/**
 * Property tests for Health Agent formatting logic.
 * Feature: genai-operations-analytics-tool
 *
 * These tests exercise a pure TypeScript implementation that mirrors the
 * Python Health Agent's `_format_health_events` behaviour so it can be
 * verified with fast-check without calling boto3.
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { arbHealthEvent } from '../generators/health-event.gen';

// ---------------------------------------------------------------------------
// Local TypeScript implementation mirroring the Python Health Agent logic
// ---------------------------------------------------------------------------

/** A health event object matching the shape produced by the generator. */
interface HealthEvent {
  arn: string;
  service: string;
  eventTypeCode: string;
  eventTypeCategory: string;
  region: string;
  startTime: string;
  endTime?: string;
  lastUpdatedTime: string;
  statusCode: string;
  eventScopeCode: string;
}

/**
 * Format health events into a human-readable response string.
 * Mirrors `_format_health_events` in `agents/health-agent/main.py`.
 *
 * The output MUST contain for each event:
 *  - event type (eventTypeCategory)
 *  - affected service
 *  - affected region
 *  - start time
 *  - current status (statusCode)
 */
function formatHealthEvents(events: HealthEvent[]): string {
  if (events.length === 0) {
    return 'No matching health events found for the specified criteria.';
  }

  const lines: string[] = [`AWS Health Events (${events.length} found)`, ''];
  for (const event of events) {
    const eventType = event.eventTypeCategory ?? 'N/A';
    const service = event.service ?? 'N/A';
    const region = event.region ?? 'N/A';
    const startTime = event.startTime ?? 'N/A';
    const status = event.statusCode ?? 'N/A';
    const eventCode = event.eventTypeCode ?? 'N/A';

    lines.push(
      `  Type: ${eventType} | Service: ${service} | ` +
        `Region: ${region} | Start: ${startTime} | ` +
        `Status: ${status} | Code: ${eventCode}`,
    );
  }

  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Health Agent property tests', () => {
  /**
   * Property 6: Health event formatting includes required fields
   *
   * For any health event object, the Health Agent's response formatter
   * should produce output containing the event type, affected services,
   * affected regions, start time, and current status.
   *
   * **Validates: Requirements 3.3**
   */
  it('Property 6: Health event formatting includes required fields — Feature: genai-operations-analytics-tool, Property 6: Health event formatting includes required fields', () => {
    fc.assert(
      fc.property(
        fc.array(arbHealthEvent, { minLength: 1, maxLength: 10 }),
        (events) => {
          const output = formatHealthEvents(events);

          // Header must include the event count
          expect(output).toContain(`${events.length} found`);

          // Each event's required fields must appear in the output
          for (const event of events) {
            expect(output).toContain(`Type: ${event.eventTypeCategory}`);
            expect(output).toContain(`Service: ${event.service}`);
            expect(output).toContain(`Region: ${event.region}`);
            expect(output).toContain(`Start: ${event.startTime}`);
            expect(output).toContain(`Status: ${event.statusCode}`);
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * Supplementary check: empty events list returns the no-events message.
   * This validates Requirement 3.4 (no-events-found confirmation).
   */
  it('Property 6 (empty case): empty events list returns no-events message — Feature: genai-operations-analytics-tool, Property 6: Health event formatting includes required fields', () => {
    const output = formatHealthEvents([]);
    expect(output).toBe(
      'No matching health events found for the specified criteria.',
    );
  });
});
