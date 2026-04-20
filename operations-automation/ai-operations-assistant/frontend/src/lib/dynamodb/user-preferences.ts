/**
 * G.O.A.T. - GenAI Operations Analytics Tool
 * DynamoDB data access layer for user preferences
 *
 * PK: USER#<userId>  SK: PREFS
 *
 * Validates: Requirements 13.4
 */

import {
  DynamoDBClient,
  PutItemCommand,
  GetItemCommand,
  UpdateItemCommand,
} from '@aws-sdk/client-dynamodb';
import { marshall, unmarshall } from '@aws-sdk/util-dynamodb';
import type { UserPreferencesItem } from '@shared/types';

const client = new DynamoDBClient({});
const TABLE_NAME =
  import.meta.env.VITE_USER_PREFERENCES_TABLE ?? 'GOATUserPreferences';

// ---------------------------------------------------------------------------
// Key Helpers
// ---------------------------------------------------------------------------

export function userPK(userId: string): string {
  return `USER#${userId}`;
}

const PREFS_SK = 'PREFS';

// ---------------------------------------------------------------------------
// Default Preferences
// ---------------------------------------------------------------------------

export const DEFAULT_PREFERENCES: Omit<UserPreferencesItem, 'PK' | 'SK' | 'updatedAt'> = {
  defaultAccount: '',
  preferredTemplates: [],
  displaySettings: {
    theme: 'light',
    responseFormat: 'detailed',
    chartType: 'bar',
  },
};

// ---------------------------------------------------------------------------
// CRUD Operations
// ---------------------------------------------------------------------------

/** Retrieve user preferences. Returns defaults if none exist yet. */
export async function getPreferences(
  userId: string,
): Promise<UserPreferencesItem> {
  const { Item } = await client.send(
    new GetItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({ PK: userPK(userId), SK: PREFS_SK }),
    }),
  );

  if (Item) return unmarshall(Item) as UserPreferencesItem;

  // Return sensible defaults when no record exists
  return {
    PK: userPK(userId),
    SK: PREFS_SK,
    ...DEFAULT_PREFERENCES,
    updatedAt: new Date().toISOString(),
  };
}

/** Create or fully replace user preferences. */
export async function savePreferences(
  userId: string,
  prefs: Omit<UserPreferencesItem, 'PK' | 'SK' | 'updatedAt'>,
): Promise<UserPreferencesItem> {
  const item: UserPreferencesItem = {
    PK: userPK(userId),
    SK: PREFS_SK,
    defaultAccount: prefs.defaultAccount,
    preferredTemplates: prefs.preferredTemplates,
    displaySettings: prefs.displaySettings,
    updatedAt: new Date().toISOString(),
  };

  await client.send(
    new PutItemCommand({
      TableName: TABLE_NAME,
      Item: marshall(item, { removeUndefinedValues: true }),
    }),
  );

  return item;
}

/** Partially update user preferences (merge with existing). */
export async function updatePreferences(
  userId: string,
  updates: Partial<Omit<UserPreferencesItem, 'PK' | 'SK' | 'updatedAt'>>,
): Promise<UserPreferencesItem> {
  const current = await getPreferences(userId);

  const merged: UserPreferencesItem = {
    ...current,
    defaultAccount: updates.defaultAccount ?? current.defaultAccount,
    preferredTemplates: updates.preferredTemplates ?? current.preferredTemplates,
    displaySettings: {
      ...current.displaySettings,
      ...(updates.displaySettings ?? {}),
    },
    updatedAt: new Date().toISOString(),
  };

  await client.send(
    new PutItemCommand({
      TableName: TABLE_NAME,
      Item: marshall(merged, { removeUndefinedValues: true }),
    }),
  );

  return merged;
}

/** Update only the default account. */
export async function setDefaultAccount(
  userId: string,
  accountId: string,
): Promise<void> {
  await client.send(
    new UpdateItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({ PK: userPK(userId), SK: PREFS_SK }),
      UpdateExpression: 'SET defaultAccount = :a, updatedAt = :now',
      ExpressionAttributeValues: marshall({
        ':a': accountId,
        ':now': new Date().toISOString(),
      }),
    }),
  );
}

/** Update only the display settings. */
export async function setDisplaySettings(
  userId: string,
  settings: Partial<UserPreferencesItem['displaySettings']>,
): Promise<void> {
  const current = await getPreferences(userId);
  const merged = { ...current.displaySettings, ...settings };

  await client.send(
    new UpdateItemCommand({
      TableName: TABLE_NAME,
      Key: marshall({ PK: userPK(userId), SK: PREFS_SK }),
      UpdateExpression: 'SET displaySettings = :ds, updatedAt = :now',
      ExpressionAttributeValues: marshall({
        ':ds': merged,
        ':now': new Date().toISOString(),
      }),
    }),
  );
}
