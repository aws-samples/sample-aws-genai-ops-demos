/**
 * fast-check arbitraries for knowledge article objects.
 * Validates: Requirements 9.1
 */
import fc from 'fast-check';
import type { KnowledgeArticleItem } from '@shared/types';
import { DOMAINS } from '@shared/constants';

/** ISO timestamp */
const arbTimestamp = fc
  .date({ min: new Date('2024-01-01'), max: new Date('2025-12-31') })
  .map((d) => d.toISOString());

/** Article category */
const arbCategory = fc.constantFrom(
  'cost-optimization', 'security', 'performance',
  'health-events', 'support-cases', 'trusted-advisor',
  'general',
);

/** Source agent domains */
const arbSourceAgents = fc.subarray([...DOMAINS], { minLength: 1, maxLength: DOMAINS.length });

/** Tags for articles */
const arbTags = fc.array(
  fc.string({ minLength: 2, maxLength: 20 }),
  { minLength: 0, maxLength: 5 },
);

/** A complete KnowledgeArticleItem matching the DynamoDB schema */
export const arbKnowledgeArticleItem: fc.Arbitrary<KnowledgeArticleItem> = fc
  .tuple(fc.uuidV(4), arbCategory, arbTimestamp)
  .chain(([articleId, category, createdAt]) =>
    fc.record({
      PK: fc.constant(`ARTICLE#${articleId}`),
      SK: fc.constant('META'),
      GSI1PK: fc.constant(`CATEGORY#${category}`),
      GSI1SK: fc.constant(createdAt),
      title: fc.string({ minLength: 5, maxLength: 120 }),
      category: fc.constant(category),
      sourceAgents: arbSourceAgents,
      originalQuery: fc.string({ minLength: 5, maxLength: 300 }),
      content: fc.string({ minLength: 20, maxLength: 2000 }),
      createdAt: fc.constant(createdAt),
      createdBy: fc.stringMatching(/^user-[a-z0-9]{6}$/),
      tags: arbTags,
    }),
  );

/** Minimal knowledge article (edge case — no tags, single source agent) */
export const arbKnowledgeArticleMinimal: fc.Arbitrary<KnowledgeArticleItem> = fc
  .tuple(fc.uuidV(4), arbCategory, arbTimestamp)
  .chain(([articleId, category, createdAt]) =>
    fc.record({
      PK: fc.constant(`ARTICLE#${articleId}`),
      SK: fc.constant('META'),
      GSI1PK: fc.constant(`CATEGORY#${category}`),
      GSI1SK: fc.constant(createdAt),
      title: fc.string({ minLength: 5, maxLength: 120 }),
      category: fc.constant(category),
      sourceAgents: fc.constant([DOMAINS[0]]),
      originalQuery: fc.string({ minLength: 5, maxLength: 300 }),
      content: fc.string({ minLength: 20, maxLength: 500 }),
      createdAt: fc.constant(createdAt),
      createdBy: fc.stringMatching(/^user-[a-z0-9]{6}$/),
      tags: fc.constant([] as string[]),
    }),
  );

/** Webhook export payload for a knowledge article */
export const arbKnowledgeArticleExport = fc.record({
  title: fc.string({ minLength: 5, maxLength: 120 }),
  content: fc.string({ minLength: 20, maxLength: 2000 }),
  category: arbCategory,
  metadata: fc.record({
    sourceAgents: arbSourceAgents,
    originalQuery: fc.string({ minLength: 5, maxLength: 300 }),
    tags: arbTags,
  }),
  timestamp: arbTimestamp,
});
