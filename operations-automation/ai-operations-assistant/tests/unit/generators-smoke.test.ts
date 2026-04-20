/**
 * Smoke tests for all fast-check generators.
 * Verifies each generator compiles and produces structurally valid data.
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { arbCostAndUsageParams, arbCostForecastParams, arbRecommendationsParams, arbCostDataResponse, arbCostComparison } from '../generators/cost-data.gen';
import { arbHealthEvent, arbHealthEventsParams, arbHealthEventResponse } from '../generators/health-event.gen';
import { arbSupportCase, arbSupportCaseResponse } from '../generators/support-case.gen';
import { arbTARecommendation, arbTAChecksParams, arbTAResponse } from '../generators/ta-recommendation.gen';
import { arbPromptTemplate, arbPromptTemplateNoParams } from '../generators/prompt-template.gen';
import { arbKnowledgeArticleItem, arbKnowledgeArticleExport } from '../generators/knowledge-article.gen';
import { arbConversationItem, arbMessageSequence, arbEmptyConversation } from '../generators/conversation.gen';

const NUM_RUNS = 50;

describe('Generator smoke tests', () => {
  // --- Cost ---
  it('arbCostAndUsageParams produces valid objects', () => {
    fc.assert(
      fc.property(arbCostAndUsageParams, (p) => {
        expect(p.startDate).toBeTruthy();
        expect(p.endDate).toBeTruthy();
        expect(['DAILY', 'MONTHLY']).toContain(p.granularity);
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('arbCostDataResponse has required metadata fields', () => {
    fc.assert(
      fc.property(arbCostDataResponse, (r) => {
        expect(r.success).toBe(true);
        expect(r.domain).toBe('cost');
        expect(r.metadata.sourceApi).toBeTruthy();
        expect(r.metadata.queryTimestamp).toBeTruthy();
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('arbCostComparison includes percentage change', () => {
    fc.assert(
      fc.property(arbCostComparison, (c) => {
        expect(typeof c.percentageChange).toBe('number');
        expect(c.currentPeriod.total).toBeGreaterThan(0);
      }),
      { numRuns: NUM_RUNS },
    );
  });

  // --- Health ---
  it('arbHealthEvent includes required fields per Req 3.3', () => {
    fc.assert(
      fc.property(arbHealthEvent, (e) => {
        expect(e.service).toBeTruthy();
        expect(e.region).toBeTruthy();
        expect(e.startTime).toBeTruthy();
        expect(e.statusCode).toBeTruthy();
        expect(e.eventTypeCategory).toBeTruthy();
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('arbHealthEventResponse has correct domain', () => {
    fc.assert(
      fc.property(arbHealthEventResponse, (r) => {
        expect(r.domain).toBe('health');
        expect(r.success).toBe(true);
      }),
      { numRuns: NUM_RUNS },
    );
  });

  // --- Support ---
  it('arbSupportCase includes required fields per Req 4.3', () => {
    fc.assert(
      fc.property(arbSupportCase, (c) => {
        expect(c.caseId).toBeTruthy();
        expect(c.subject).toBeTruthy();
        expect(c.status).toBeTruthy();
        expect(c.severityCode).toBeTruthy();
        expect(c.timeCreated).toBeTruthy();
      }),
      { numRuns: NUM_RUNS },
    );
  });

  // --- Trusted Advisor ---
  it('arbTARecommendation has pillar assignment per Req 5.3', () => {
    fc.assert(
      fc.property(arbTARecommendation, (r) => {
        expect(['cost_optimizing', 'security', 'performance', 'fault_tolerance', 'service_limits']).toContain(r.pillar);
        expect(r.status).toBeTruthy();
        expect(r.resourcesSummary.resourcesProcessed).toBeGreaterThanOrEqual(0);
      }),
      { numRuns: NUM_RUNS },
    );
  });

  // --- Prompt Templates ---
  it('arbPromptTemplate has parameterized placeholders', () => {
    fc.assert(
      fc.property(arbPromptTemplate, (t) => {
        expect(t.id).toBeTruthy();
        expect(t.description.length).toBeGreaterThan(0);
        expect(['health', 'trusted_advisor', 'support', 'cost']).toContain(t.category);
        // Every parameter name should appear as {{name}} in the template
        for (const p of t.parameters) {
          expect(t.template).toContain(`{{${p.name}}}`);
        }
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('arbPromptTemplateNoParams has empty parameters array', () => {
    fc.assert(
      fc.property(arbPromptTemplateNoParams, (t) => {
        expect(t.parameters).toHaveLength(0);
      }),
      { numRuns: NUM_RUNS },
    );
  });

  // --- Knowledge Articles ---
  it('arbKnowledgeArticleItem has correct DynamoDB key structure', () => {
    fc.assert(
      fc.property(arbKnowledgeArticleItem, (a) => {
        expect(a.PK).toMatch(/^ARTICLE#/);
        expect(a.SK).toBe('META');
        expect(a.GSI1PK).toMatch(/^CATEGORY#/);
        expect(a.sourceAgents.length).toBeGreaterThan(0);
        expect(a.createdBy).toBeTruthy();
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('arbKnowledgeArticleExport has webhook payload structure', () => {
    fc.assert(
      fc.property(arbKnowledgeArticleExport, (e) => {
        expect(e.title).toBeTruthy();
        expect(e.content).toBeTruthy();
        expect(e.timestamp).toBeTruthy();
        expect(e.metadata.sourceAgents.length).toBeGreaterThan(0);
      }),
      { numRuns: NUM_RUNS },
    );
  });

  // --- Conversations ---
  it('arbMessageSequence alternates user/assistant roles', () => {
    fc.assert(
      fc.property(arbMessageSequence, (msgs) => {
        expect(msgs.length).toBeGreaterThan(0);
        // First message is always from user
        expect(msgs[0].role).toBe('user');
        // Roles alternate
        for (let i = 1; i < msgs.length; i++) {
          expect(msgs[i].role).toBe(i % 2 === 0 ? 'user' : 'assistant');
        }
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('arbConversationItem has correct DynamoDB key structure', () => {
    fc.assert(
      fc.property(arbConversationItem, (c) => {
        expect(c.PK).toMatch(/^USER#/);
        expect(c.SK).toMatch(/^CONV#/);
        expect(c.TTL).toBeGreaterThan(0);
        expect(['active', 'archived']).toContain(c.status);
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('arbEmptyConversation has zero messages', () => {
    fc.assert(
      fc.property(arbEmptyConversation, (c) => {
        expect(c.messages).toHaveLength(0);
        expect(c.status).toBe('active');
      }),
      { numRuns: NUM_RUNS },
    );
  });
});
