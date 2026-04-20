/**
 * G.O.A.T. - GenAI Operations Analytics Tool
 * DynamoDB data access layer for conversations
 *
 * PK: USER#<userId>  SK: CONV#<conversationId>
 * TTL: 90-day archival via CONVERSATION_TTL_DAYS constant
 *
 * Validates: Requirements 9.1, 9.2, 13.1, 13.3, 13.5
 */

import {
  DynamoDBClient,
  QueryCommand,
  PutItemCommand,
  GetItemCommand,
  UpdateItemCommand,
  DeleteItemCommand,
} from '@aws-sdk/client-dynamodb';
import { marshall, unmarshall } from '@aws-sdk/util-dynamodb';
import type { ConversationItem, Message } from '@shared/types';
import { CONVERSATION_TTL_DAYS } from '@shared/constants';

const client = new DynamoDBClient({});
const TABLE_NAME = import.meta.env.VITE_CONVERSATIONS_TABLE ?? 'GOATConversations';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Compute epoch-seconds TTL that is `days` from now. */
export function computeTTL(days: number = CONVERSATION_TTL_DAYS): number {
  return Math.floor(Date.now() / 1000) + days * 24 * 60 * 60;
}

/** Build the composite PK for a user. */
export function userPK(userId: string): string {
  return `USER#${userId}`;
}

/** Build the composite SK for a conversation. */
export function convSK(conversationId: string): string {
  return `CONV#${conversationId}`;
}

// ---------------------------------------------------------------------------
// CRUD Operations
// ---------------------------------------------------------------------------

/** Create a new conversation with an initial (possibly empty) message list. */
export async function createConversation(
  userId: string,
  conversationId: string,
  title: string,
  messages: Message[] = [],
): Promise<ConversationItem> {
  const now = new Date().toISOString();
  const item: ConversationItem = {
    PK: userPK(userId),
    SK: convSK(conversationId),
    title,
    createdAt: now,
    updatedAt: now,
    status: 'active',
    messages: sortMessages(messages),
    TTL: computeTTL(),
  };

  await client.send(
    new PutItemCommand({
      TableName: TABLE_NAME,
      Item: marshall(item, { removeUndefinedValues: true }),
    }),
  );

  return item;
}

/** Retrieve a single conversation by userId + conversationId. */
export async function getConversation(
  userId: string,
  conversationId: string,
): Promise<ConversationItem | null> {
  const { Item } = await client.send(
    new GetItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({ PK: userPK(userId), SK: convSK(conversationId) }),
    }),
  );

  return Item ? (unmarshall(Item) as ConversationItem) : null;
}

/** List all active conversations for a user, newest first. */
export async function listConversations(
  userId: string,
): Promise<ConversationItem[]> {
  const { Items = [] } = await client.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: 'PK = :pk AND begins_with(SK, :skPrefix)',
      FilterExpression: '#s = :active',
      ExpressionAttributeNames: { '#s': 'status' },
      ExpressionAttributeValues: marshall({
        ':pk': userPK(userId),
        ':skPrefix': 'CONV#',
        ':active': 'active',
      }),
      ScanIndexForward: false,
    }),
  );

  return Items.map((i) => unmarshall(i) as ConversationItem).sort(
    (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
  );
}

/**
 * Append a message to an existing conversation.
 * Preserves message ordering by timestamp and refreshes the TTL.
 */
export async function addMessage(
  userId: string,
  conversationId: string,
  message: Message,
): Promise<ConversationItem | null> {
  const conversation = await getConversation(userId, conversationId);
  if (!conversation) return null;

  const updatedMessages = sortMessages([...conversation.messages, message]);
  const now = new Date().toISOString();

  await client.send(
    new UpdateItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({ PK: userPK(userId), SK: convSK(conversationId) }),
      UpdateExpression:
        'SET messages = :msgs, updatedAt = :now, #ttl = :ttl',
      ExpressionAttributeNames: { '#ttl': 'TTL' },
      ExpressionAttributeValues: marshall({
        ':msgs': updatedMessages,
        ':now': now,
        ':ttl': computeTTL(),
      }),
    }),
  );

  return { ...conversation, messages: updatedMessages, updatedAt: now, TTL: computeTTL() };
}

/** Update the title of a conversation. */
export async function updateConversationTitle(
  userId: string,
  conversationId: string,
  title: string,
): Promise<void> {
  await client.send(
    new UpdateItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({ PK: userPK(userId), SK: convSK(conversationId) }),
      UpdateExpression: 'SET title = :t, updatedAt = :now',
      ExpressionAttributeValues: marshall({
        ':t': title,
        ':now': new Date().toISOString(),
      }),
    }),
  );
}

/** Delete a conversation permanently. */
export async function deleteConversation(
  userId: string,
  conversationId: string,
): Promise<void> {
  await client.send(
    new DeleteItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({ PK: userPK(userId), SK: convSK(conversationId) }),
    }),
  );
}

// ---------------------------------------------------------------------------
// Archival Logic (90-day TTL)
// ---------------------------------------------------------------------------

/**
 * Archive conversations that have not been updated within the TTL window.
 * Sets status to 'archived' so they no longer appear in the active list.
 * DynamoDB TTL will eventually hard-delete the item.
 */
export async function archiveStaleConversations(
  userId: string,
): Promise<string[]> {
  const { Items = [] } = await client.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: 'PK = :pk AND begins_with(SK, :skPrefix)',
      FilterExpression: '#s = :active',
      ExpressionAttributeNames: { '#s': 'status' },
      ExpressionAttributeValues: marshall({
        ':pk': userPK(userId),
        ':skPrefix': 'CONV#',
        ':active': 'active',
      }),
    }),
  );

  const cutoff = Date.now() - CONVERSATION_TTL_DAYS * 24 * 60 * 60 * 1000;
  const archived: string[] = [];

  for (const raw of Items) {
    const conv = unmarshall(raw) as ConversationItem;
    if (new Date(conv.updatedAt).getTime() < cutoff) {
      await client.send(
        new UpdateItemCommand({
          TableName: TABLE_NAME,
          Key: marshall({ PK: conv.PK, SK: conv.SK }),
          UpdateExpression: 'SET #s = :archived',
          ExpressionAttributeNames: { '#s': 'status' },
          ExpressionAttributeValues: marshall({ ':archived': 'archived' }),
        }),
      );
      archived.push(conv.SK.replace('CONV#', ''));
    }
  }

  return archived;
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

/** Sort messages by timestamp ascending to preserve ordering. */
export function sortMessages(messages: Message[]): Message[] {
  return [...messages].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  );
}
