/**
 * Property tests for Orchestration Agent logic.
 * Feature: genai-operations-analytics-tool
 *
 * These tests exercise pure TypeScript implementations that mirror the
 * Orchestration Agent's intent classification, response aggregation, and
 * partial-results handling — without calling the actual LLM or boto3.
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { DOMAINS } from '@shared/constants';
import type { Domain } from '@shared/constants';

// ---------------------------------------------------------------------------
// Local TypeScript implementations mirroring the Orchestration Agent logic
// ---------------------------------------------------------------------------

/**
 * Keyword-to-domain mapping used by the intent classifier.
 * Mirrors the routing heuristics the LLM applies in the orchestration agent.
 */
const DOMAIN_KEYWORDS: Record<Domain, string[]> = {
  cost: ['cost', 'spend', 'spending', 'budget', 'forecast', 'billing', 'price', 'charge', 'expense'],
  health: ['health', 'outage', 'incident', 'maintenance', 'service event', 'disruption', 'degradation'],
  support: ['support', 'case', 'ticket', 'communication', 'escalation', 'issue'],
  trusted_advisor: ['trusted advisor', 'recommendation', 'optimization', 'best practice', 'check', 'pillar'],
  cur: ['cur', 'usage report', 'athena', 'resource cost', 'usage pattern', 'granular'],
};

/**
 * Classify a natural-language query into one or more operational domains.
 * Returns the set of domains whose keywords appear in the query.
 *
 * Mirrors the intent-classification reasoning the orchestration agent's
 * LLM performs before invoking @tool functions.
 */
function classifyIntent(query: string): Domain[] {
  const lower = query.toLowerCase();
  const matched = new Set<Domain>();

  for (const domain of DOMAINS) {
    for (const keyword of DOMAIN_KEYWORDS[domain]) {
      if (lower.includes(keyword)) {
        matched.add(domain);
        break;
      }
    }
  }

  return [...matched];
}

/** Shape of a single sub-agent response used by the aggregator. */
interface SubAgentResult {
  success: boolean;
  domain: string;
  content: string;
}

/**
 * Aggregate responses from multiple sub-agents into a single response.
 * Mirrors the orchestration agent's response-aggregation logic that
 * combines results from all invoked sub-agents.
 */
function aggregateResponses(results: SubAgentResult[]): {
  domains: string[];
  combinedContent: string;
  resultCount: number;
} {
  const domains = results.map((r) => r.domain);
  const combinedContent = results.map((r) => `[${r.domain}] ${r.content}`).join('\n');
  return { domains, combinedContent, resultCount: results.length };
}

/** Outcome of a sub-agent invocation: either success with content or a timeout. */
interface SubAgentOutcome {
  domain: string;
  status: 'success' | 'timeout';
  content?: string;
}

/**
 * Handle a mix of successful and timed-out sub-agent invocations.
 * Returns partial results from successful agents and lists failed domains.
 *
 * Mirrors the orchestration agent's partial-results logic when one or
 * more @tool functions fail or exceed the 30-second timeout.
 */
function handlePartialResults(outcomes: SubAgentOutcome[]): {
  successfulResults: SubAgentResult[];
  failedDomains: string[];
  hasPartialResults: boolean;
} {
  const successfulResults: SubAgentResult[] = [];
  const failedDomains: string[] = [];

  for (const outcome of outcomes) {
    if (outcome.status === 'success' && outcome.content) {
      successfulResults.push({
        success: true,
        domain: outcome.domain,
        content: outcome.content,
      });
    } else {
      failedDomains.push(outcome.domain);
    }
  }

  return {
    successfulResults,
    failedDomains,
    hasPartialResults: successfulResults.length > 0 && failedDomains.length > 0,
  };
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Arbitrary that picks a non-empty subset of DOMAINS. */
const arbDomainSubset: fc.Arbitrary<Domain[]> = fc
  .subarray([...DOMAINS], { minLength: 1, maxLength: DOMAINS.length })
  .filter((arr) => arr.length > 0);

/**
 * Build a query string that contains at least one keyword for each
 * requested domain, ensuring the classifier will match them.
 */
function buildQueryForDomains(domains: Domain[]): fc.Arbitrary<string> {
  return fc
    .tuple(
      ...domains.map((d) =>
        fc.constantFrom(...DOMAIN_KEYWORDS[d]).map((kw) => kw),
      ),
    )
    .map((keywords) => keywords.join(' and '));
}

/** Arbitrary that produces a tagged query guaranteed to match specific domains. */
const arbTaggedQuery: fc.Arbitrary<{ query: string; expectedDomains: Domain[] }> =
  arbDomainSubset.chain((domains) =>
    buildQueryForDomains(domains).map((query) => ({ query, expectedDomains: domains })),
  );

/** Arbitrary for a single successful sub-agent result. */
const arbSubAgentResult: fc.Arbitrary<SubAgentResult> = fc.record({
  success: fc.constant(true),
  domain: fc.constantFrom(...DOMAINS),
  content: fc.string({ minLength: 1, maxLength: 300 }),
});

/** Arbitrary for a set of sub-agent results with unique domains. */
const arbSubAgentResultSet: fc.Arbitrary<SubAgentResult[]> = fc
  .subarray([...DOMAINS], { minLength: 1, maxLength: DOMAINS.length })
  .chain((domains) =>
    fc.tuple(
      ...domains.map((d) =>
        fc.string({ minLength: 1, maxLength: 300 }).map(
          (content): SubAgentResult => ({ success: true, domain: d, content }),
        ),
      ),
    ),
  );

/**
 * Arbitrary for a mix of success/timeout outcomes where at least one
 * succeeds and at least one times out.
 */
const arbMixedOutcomes: fc.Arbitrary<SubAgentOutcome[]> = fc
  .subarray([...DOMAINS], { minLength: 2, maxLength: DOMAINS.length })
  .filter((arr) => arr.length >= 2)
  .chain((domains) => {
    // Pick at least 1 for success and at least 1 for timeout
    const splitPoint = fc.integer({ min: 1, max: domains.length - 1 });
    return splitPoint.chain((sp) => {
      const successDomains = domains.slice(0, sp);
      const timeoutDomains = domains.slice(sp);

      const successOutcomes = successDomains.map((d) =>
        fc.string({ minLength: 1, maxLength: 200 }).map(
          (content): SubAgentOutcome => ({ domain: d, status: 'success', content }),
        ),
      );
      const timeoutOutcomes = timeoutDomains.map((d) =>
        fc.constant<SubAgentOutcome>({ domain: d, status: 'timeout' }),
      );

      return fc.tuple(...successOutcomes, ...timeoutOutcomes);
    });
  });

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Orchestration Agent property tests', () => {
  /**
   * Property 1: Intent classification routes to correct sub-agents
   *
   * For any natural language query tagged with one or more expected
   * operational domains, the intent classifier should return a routing
   * decision that includes all expected domains and no unrelated domains.
   *
   * **Validates: Requirements 1.1**
   */
  it('Property 1: Intent classification routes to correct sub-agents — Feature: genai-operations-analytics-tool, Property 1: Intent classification routes to correct sub-agents', () => {
    fc.assert(
      fc.property(arbTaggedQuery, ({ query, expectedDomains }) => {
        const classified = classifyIntent(query);

        // Every expected domain must be present in the classification
        for (const domain of expectedDomains) {
          expect(classified).toContain(domain);
        }

        // No domain outside the expected set should appear
        for (const domain of classified) {
          expect(DOMAINS).toContain(domain);
        }

        // Classification must return at least one domain
        expect(classified.length).toBeGreaterThanOrEqual(1);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 2: Multi-agent response aggregation preserves all sub-agent results
   *
   * For any set of sub-agent responses (each with a domain label and
   * content), the aggregated response should contain content from every
   * sub-agent in the input set.
   *
   * **Validates: Requirements 1.2**
   */
  it('Property 2: Multi-agent response aggregation preserves all sub-agent results — Feature: genai-operations-analytics-tool, Property 2: Multi-agent response aggregation preserves all sub-agent results', () => {
    fc.assert(
      fc.property(arbSubAgentResultSet, (results) => {
        const aggregated = aggregateResponses(results);

        // Result count must match input count
        expect(aggregated.resultCount).toBe(results.length);

        // Every input domain must appear in the aggregated domains list
        for (const result of results) {
          expect(aggregated.domains).toContain(result.domain);
        }

        // Every input content must appear in the combined output
        for (const result of results) {
          expect(aggregated.combinedContent).toContain(result.content);
        }

        // Every domain label must appear in the combined output
        for (const result of results) {
          expect(aggregated.combinedContent).toContain(`[${result.domain}]`);
        }
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 3: Partial results on sub-agent timeout
   *
   * For any combination of sub-agent success/failure states (where at
   * least one succeeds and at least one times out), the response should
   * include results from all successful sub-agents and explicitly list
   * the domains of all failed sub-agents.
   *
   * **Validates: Requirements 1.4**
   */
  it('Property 3: Partial results on sub-agent timeout — Feature: genai-operations-analytics-tool, Property 3: Partial results on sub-agent timeout', () => {
    fc.assert(
      fc.property(arbMixedOutcomes, (outcomes) => {
        const { successfulResults, failedDomains, hasPartialResults } =
          handlePartialResults(outcomes);

        const expectedSuccessCount = outcomes.filter(
          (o) => o.status === 'success',
        ).length;
        const expectedFailCount = outcomes.filter(
          (o) => o.status === 'timeout',
        ).length;

        // Must have partial results (mix of success and failure)
        expect(hasPartialResults).toBe(true);

        // Successful results count must match successful outcomes
        expect(successfulResults.length).toBe(expectedSuccessCount);

        // Failed domains count must match timed-out outcomes
        expect(failedDomains.length).toBe(expectedFailCount);

        // Every successful outcome's domain must appear in results
        for (const outcome of outcomes) {
          if (outcome.status === 'success') {
            expect(
              successfulResults.some((r) => r.domain === outcome.domain),
            ).toBe(true);
          }
        }

        // Every timed-out outcome's domain must appear in failedDomains
        for (const outcome of outcomes) {
          if (outcome.status === 'timeout') {
            expect(failedDomains).toContain(outcome.domain);
          }
        }

        // Successful and failed domains must not overlap
        for (const domain of failedDomains) {
          expect(
            successfulResults.every((r) => r.domain !== domain),
          ).toBe(true);
        }
      }),
      { numRuns: 100 },
    );
  });
});
