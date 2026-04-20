/**
 * G.O.A.T. - GenAI Operations Analytics Tool
 * DynamoDB data access layer for knowledge articles
 *
 * PK: ARTICLE#<articleId>  SK: META
 * GSI1PK: CATEGORY#<category>  GSI1SK: <createdAt>
 *
 * Validates: Requirements 9.1, 9.2, 9.3, 9.5
 */

import {
  DynamoDBClient,
  PutItemCommand,
  GetItemCommand,
  DeleteItemCommand,
  QueryCommand,
  ScanCommand,
} from '@aws-sdk/client-dynamodb';
import { marshall, unmarshall } from '@aws-sdk/util-dynamodb';
import type { KnowledgeArticleItem } from '@shared/types';

const client = new DynamoDBClient({});
const TABLE_NAME =
  import.meta.env.VITE_KNOWLEDGE_ARTICLES_TABLE ?? 'GOATKnowledgeArticles';
const GSI1_NAME = 'GSI1';

// ---------------------------------------------------------------------------
// Key Helpers
// ---------------------------------------------------------------------------

export function articlePK(articleId: string): string {
  return `ARTICLE#${articleId}`;
}

export function categoryGSI1PK(category: string): string {
  return `CATEGORY#${category}`;
}

// ---------------------------------------------------------------------------
// CRUD Operations
// ---------------------------------------------------------------------------

/** Create a new knowledge article. */
export async function createArticle(
  article: Omit<KnowledgeArticleItem, 'PK' | 'SK' | 'GSI1PK' | 'GSI1SK'> & {
    articleId: string;
  },
): Promise<KnowledgeArticleItem> {
  const item: KnowledgeArticleItem = {
    PK: articlePK(article.articleId),
    SK: 'META',
    GSI1PK: categoryGSI1PK(article.category),
    GSI1SK: article.createdAt,
    title: article.title,
    category: article.category,
    sourceAgents: article.sourceAgents,
    originalQuery: article.originalQuery,
    content: article.content,
    createdAt: article.createdAt,
    createdBy: article.createdBy,
    tags: article.tags,
  };

  await client.send(
    new PutItemCommand({
      TableName: TABLE_NAME,
      Item: marshall(item, { removeUndefinedValues: true }),
    }),
  );

  return item;
}

/** Retrieve a single article by its ID. */
export async function getArticle(
  articleId: string,
): Promise<KnowledgeArticleItem | null> {
  const { Item } = await client.send(
    new GetItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({ PK: articlePK(articleId), SK: 'META' }),
    }),
  );

  return Item ? (unmarshall(Item) as KnowledgeArticleItem) : null;
}

/** Delete an article by its ID. */
export async function deleteArticle(articleId: string): Promise<void> {
  await client.send(
    new DeleteItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({ PK: articlePK(articleId), SK: 'META' }),
    }),
  );
}

/** List articles by category using GSI1, newest first. */
export async function listArticlesByCategory(
  category: string,
): Promise<KnowledgeArticleItem[]> {
  const { Items = [] } = await client.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      IndexName: GSI1_NAME,
      KeyConditionExpression: 'GSI1PK = :gsi1pk',
      ExpressionAttributeValues: marshall({
        ':gsi1pk': categoryGSI1PK(category),
      }),
      ScanIndexForward: false, // newest first
    }),
  );

  return Items.map((i) => unmarshall(i) as KnowledgeArticleItem);
}

// ---------------------------------------------------------------------------
// Search with Relevance Ranking
// ---------------------------------------------------------------------------

/**
 * Search articles by keyword across title, content, tags, and originalQuery.
 * Results are ranked by a simple relevance score:
 *   +3 for title match, +2 for tag match, +1 for content/query match.
 */
export async function searchArticles(
  query: string,
): Promise<KnowledgeArticleItem[]> {
  // DynamoDB doesn't support full-text search natively, so we scan and
  // filter client-side. For production, consider OpenSearch.
  const { Items = [] } = await client.send(
    new ScanCommand({ TableName: TABLE_NAME }),
  );

  const articles = Items.map((i) => unmarshall(i) as KnowledgeArticleItem);
  return rankByRelevance(articles, query);
}

/** Rank articles by simple keyword relevance scoring. */
export function rankByRelevance(
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

// ---------------------------------------------------------------------------
// Export as JSON Webhook Payload
// ---------------------------------------------------------------------------

/**
 * Build a JSON webhook payload for a knowledge article.
 * Suitable for posting to external systems (Confluence, ServiceNow, etc.).
 */
export function buildWebhookPayload(article: KnowledgeArticleItem): Record<string, unknown> {
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

/**
 * Export an article by ID as a webhook-ready JSON payload.
 * Returns null if the article does not exist.
 */
export async function exportArticleAsWebhook(
  articleId: string,
): Promise<Record<string, unknown> | null> {
  const article = await getArticle(articleId);
  if (!article) return null;
  return buildWebhookPayload(article);
}
