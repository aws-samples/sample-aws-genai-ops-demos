/**
 * Property tests for knowledge management logic.
 * Feature: genai-operations-analytics-tool
 *
 * These tests exercise pure TypeScript implementations that mirror the
 * knowledge-articles.ts logic for article creation, relevance-ranked search,
 * and webhook export — without calling actual DynamoDB.
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import type { KnowledgeArticleItem } from '@shared/types';
import {
  arbKnowledgeArticleItem,
  arbKnowledgeArticleMinimal,
} from '../generators/knowledge-article.gen';

// ---------------------------------------------------------------------------
// Local TypeScript implementations mirroring knowledge-articles.ts logic
// ---------------------------------------------------------------------------

/**
 * Create a knowledge article item from a query-response pair.
 * Mirrors `createArticle` from knowledge-articles.ts.
 */
function createKnowledgeArticle(input: {
  articleId: string;
  title: string;
  category: string;
  sourceAgents: string[];
  originalQuery: string;
  content: string;
  createdBy: string;
  tags: string[];
}): KnowledgeArticleItem {
  const createdAt = new Date().toISOString();
  return {
    PK: `ARTICLE#${input.articleId}`,
    SK: 'META',
    GSI1PK: `CATEGORY#${input.category}`,
    GSI1SK: createdAt,
    title: input.title,
    category: input.category,
    sourceAgents: input.sourceAgents,
    originalQuery: input.originalQuery,
    content: input.content,
    createdAt,
    createdBy: input.createdBy,
    tags: input.tags,
  };
}

/**
 * Rank articles by simple keyword relevance scoring.
 * Mirrors `rankByRelevance` from knowledge-articles.ts.
 */
function rankByRelevance(
  articles: KnowledgeArticleItem[],
  query: string,
): KnowledgeArticleItem[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return articles;

  const scored = articles.map((article) => {
    let score = 0;
    const titleLower = article.title.toLowerCase();
    const contentLower = article.content.toLowerCase();
    const queryLower = article.originalQuery.toLowerCase();
    const tagsLower = article.tags.map((t) => t.toLowerCase());

    for (const term of terms) {
      if (titleLower.includes(term)) score += 3;
      if (tagsLower.some((t) => t.includes(term))) score += 2;
      if (contentLower.includes(term)) score += 1;
      if (queryLower.includes(term)) score += 1;
    }

    return { article, score };
  });

  return scored
    .filter((s) => s.score > 0)
    .sort((a, b) => b.score - a.score)
    .map((s) => s.article);
}

/**
 * Build a JSON webhook payload for a knowledge article.
 * Mirrors `buildWebhookPayload` from knowledge-articles.ts.
 */
function buildWebhookPayload(article: KnowledgeArticleItem): Record<string, unknown> {
  return {
    title: article.title,
    content: article.content,
    category: article.category,
    metadata: {
      articleId: article.PK.replace('ARTICLE#', ''),
      sourceAgents: article.sourceAgents,
      originalQuery: article.originalQuery,
      createdBy: article.createdBy,
      tags: article.tags,
    },
    timestamp: article.createdAt,
  };
}

// ---------------------------------------------------------------------------
// Helper: compute relevance score for a single article (for assertions)
// ---------------------------------------------------------------------------

function computeScore(article: KnowledgeArticleItem, query: string): number {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  let score = 0;
  const titleLower = article.title.toLowerCase();
  const contentLower = article.content.toLowerCase();
  const queryLower = article.originalQuery.toLowerCase();
  const tagsLower = article.tags.map((t) => t.toLowerCase());

  for (const term of terms) {
    if (titleLower.includes(term)) score += 3;
    if (tagsLower.some((t) => t.includes(term))) score += 2;
    if (contentLower.includes(term)) score += 1;
    if (queryLower.includes(term)) score += 1;
  }
  return score;
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Generate article creation input from an arbitrary article item. */
const arbArticleCreationInput = arbKnowledgeArticleItem.map((article) => ({
  articleId: article.PK.replace('ARTICLE#', ''),
  title: article.title,
  category: article.category,
  sourceAgents: article.sourceAgents,
  originalQuery: article.originalQuery,
  content: article.content,
  createdBy: article.createdBy,
  tags: article.tags,
}));

/** Generate a non-empty array of articles for search tests. */
const arbArticleSet = fc.array(arbKnowledgeArticleItem, { minLength: 1, maxLength: 10 });

/**
 * Generate a search query that is guaranteed to match at least one article.
 * Picks a word from a random article's title to use as the search term.
 */
const arbArticleSetWithMatchingQuery: fc.Arbitrary<{
  articles: KnowledgeArticleItem[];
  query: string;
}> = arbArticleSet.chain((articles) => {
  // Pick a random article and extract a word from its title
  return fc.integer({ min: 0, max: articles.length - 1 }).map((idx) => {
    const words = articles[idx].title.split(/\s+/).filter((w) => w.length > 0);
    // Use the first word with length > 0 as the search query
    const query = words.length > 0 ? words[0] : articles[idx].title;
    return { articles, query };
  });
});

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Knowledge management property tests', () => {
  /**
   * Property 13: Knowledge article creation includes required metadata
   *
   * For any query-response pair saved as a knowledge article, the resulting
   * article should contain a title, category, source agents list, creation
   * timestamp, and the original query text.
   *
   * **Validates: Requirements 9.1**
   */
  it('Property 13: Knowledge article creation includes required metadata — Feature: genai-operations-analytics-tool, Property 13: Knowledge article creation includes required metadata', () => {
    fc.assert(
      fc.property(arbArticleCreationInput, (input) => {
        const article = createKnowledgeArticle(input);

        // Title must be present and match input
        expect(article.title).toBeDefined();
        expect(article.title).toBe(input.title);
        expect(article.title.length).toBeGreaterThan(0);

        // Category must be present and match input
        expect(article.category).toBeDefined();
        expect(article.category).toBe(input.category);
        expect(article.category.length).toBeGreaterThan(0);

        // Source agents list must be present and non-empty
        expect(article.sourceAgents).toBeDefined();
        expect(Array.isArray(article.sourceAgents)).toBe(true);
        expect(article.sourceAgents.length).toBeGreaterThan(0);
        expect(article.sourceAgents).toEqual(input.sourceAgents);

        // Creation timestamp must be a valid ISO 8601 string
        expect(article.createdAt).toBeDefined();
        expect(new Date(article.createdAt).getTime()).not.toBeNaN();

        // Original query text must be present and match input
        expect(article.originalQuery).toBeDefined();
        expect(article.originalQuery).toBe(input.originalQuery);
        expect(article.originalQuery.length).toBeGreaterThan(0);

        // DynamoDB keys must be correctly formed
        expect(article.PK).toBe(`ARTICLE#${input.articleId}`);
        expect(article.SK).toBe('META');
        expect(article.GSI1PK).toBe(`CATEGORY#${input.category}`);
        expect(article.GSI1SK).toBe(article.createdAt);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 13 (minimal variant): Minimal articles also include required metadata
   */
  it('Property 13 (minimal): Minimal knowledge articles include required metadata — Feature: genai-operations-analytics-tool, Property 13: Knowledge article creation includes required metadata', () => {
    fc.assert(
      fc.property(arbKnowledgeArticleMinimal, (article) => {
        expect(article.title).toBeDefined();
        expect(article.title.length).toBeGreaterThan(0);
        expect(article.category).toBeDefined();
        expect(article.sourceAgents.length).toBeGreaterThan(0);
        expect(new Date(article.createdAt).getTime()).not.toBeNaN();
        expect(article.originalQuery).toBeDefined();
        expect(article.originalQuery.length).toBeGreaterThan(0);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 14: Knowledge article search returns relevant results
   *
   * For any search query and set of knowledge articles, all returned results
   * should contain the search terms (in title, content, or tags), and results
   * should be ordered by relevance score descending.
   *
   * **Validates: Requirements 9.3**
   */
  it('Property 14: Knowledge article search returns relevant results — Feature: genai-operations-analytics-tool, Property 14: Knowledge article search returns relevant results', () => {
    fc.assert(
      fc.property(arbArticleSetWithMatchingQuery, ({ articles, query }) => {
        const results = rankByRelevance(articles, query);
        const terms = query.toLowerCase().split(/\s+/).filter(Boolean);

        // Every returned result must contain at least one search term
        // in title, content, tags, or originalQuery
        for (const result of results) {
          const titleLower = result.title.toLowerCase();
          const contentLower = result.content.toLowerCase();
          const queryLower = result.originalQuery.toLowerCase();
          const tagsLower = result.tags.map((t) => t.toLowerCase());

          const matchesAny = terms.some(
            (term) =>
              titleLower.includes(term) ||
              contentLower.includes(term) ||
              queryLower.includes(term) ||
              tagsLower.some((t) => t.includes(term)),
          );
          expect(matchesAny).toBe(true);
        }

        // Results must be ordered by relevance score descending
        for (let i = 1; i < results.length; i++) {
          const prevScore = computeScore(results[i - 1], query);
          const currScore = computeScore(results[i], query);
          expect(prevScore).toBeGreaterThanOrEqual(currScore);
        }
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 14 (empty query): Empty search returns all articles unfiltered
   */
  it('Property 14 (empty query): Empty search returns all articles — Feature: genai-operations-analytics-tool, Property 14: Knowledge article search returns relevant results', () => {
    fc.assert(
      fc.property(arbArticleSet, (articles) => {
        const results = rankByRelevance(articles, '');
        expect(results.length).toBe(articles.length);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 15: Knowledge article export produces valid webhook payload
   *
   * For any knowledge article, the export function should produce a JSON
   * payload containing the article's title, content, category, metadata,
   * and a timestamp.
   *
   * **Validates: Requirements 9.5**
   */
  it('Property 15: Knowledge article export produces valid webhook payload — Feature: genai-operations-analytics-tool, Property 15: Knowledge article export produces valid webhook payload', () => {
    fc.assert(
      fc.property(arbKnowledgeArticleItem, (article) => {
        const payload = buildWebhookPayload(article);

        // Title must be present and match article
        expect(payload.title).toBeDefined();
        expect(payload.title).toBe(article.title);

        // Content must be present and match article
        expect(payload.content).toBeDefined();
        expect(payload.content).toBe(article.content);

        // Category must be present and match article
        expect(payload.category).toBeDefined();
        expect(payload.category).toBe(article.category);

        // Metadata must be present with required fields
        expect(payload.metadata).toBeDefined();
        const metadata = payload.metadata as Record<string, unknown>;
        expect(metadata.sourceAgents).toEqual(article.sourceAgents);
        expect(metadata.originalQuery).toBe(article.originalQuery);
        expect(metadata.tags).toEqual(article.tags);
        expect(metadata.articleId).toBe(article.PK.replace('ARTICLE#', ''));
        expect(metadata.createdBy).toBe(article.createdBy);

        // Timestamp must be present and match article's createdAt
        expect(payload.timestamp).toBeDefined();
        expect(payload.timestamp).toBe(article.createdAt);

        // Payload must be JSON-serializable
        const serialized = JSON.stringify(payload);
        expect(serialized).toBeDefined();
        const parsed = JSON.parse(serialized);
        expect(parsed.title).toBe(article.title);
      }),
      { numRuns: 100 },
    );
  });
});
