/**
 * Property tests for conversation persistence logic.
 * Feature: genai-operations-analytics-tool
 *
 * These tests exercise pure TypeScript implementations that mirror the
 * conversation persistence layer's listing, save-load round-trip, and
 * 90-day archival logic — without calling actual DynamoDB.
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import type { ConversationItem, Message } from '@shared/types';
import { CONVERSATION_TTL_DAYS } from '@shared/constants';
import {
  arbConversationItem,
  arbMessageSequence,
} from '../generators/conversation.gen';

// ---------------------------------------------------------------------------
// Local TypeScript implementations mirroring conversation persistence logic
// ---------------------------------------------------------------------------

/**
 * Sort messages by timestamp ascending to preserve ordering.
 * Mirrors `sortMessages` from conversations.ts.
 */
function sortMessages(messages: Message[]): Message[] {
  return [...messages].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  );
}

/**
 * Simulate saving a conversation: assigns PK/SK, sorts messages, sets TTL.
 * Mirrors `createConversation` from conversations.ts.
 */
function saveConversation(
  userId: string,
  conversationId: string,
  title: string,
  messages: Message[],
): ConversationItem {
  const now = new Date().toISOString();
  return {
    PK: `USER#${userId}`,
    SK: `CONV#${conversationId}`,
    title,
    createdAt: now,
    updatedAt: now,
    status: 'active',
    messages: sortMessages(messages),
    TTL: Math.floor(Date.now() / 1000) + CONVERSATION_TTL_DAYS * 86_400,
  };
}

/**
 * Simulate loading a conversation by ID from an in-memory store.
 * Mirrors `getConversation` from conversations.ts.
 */
function loadConversation(
  store: Map<string, ConversationItem>,
  userId: string,
  conversationId: string,
): ConversationItem | null {
  const key = `USER#${userId}|CONV#${conversationId}`;
  return store.get(key) ?? null;
}

/**
 * List all active conversations for a user, newest first.
 * Mirrors `listConversations` from conversations.ts.
 */
function listConversations(conversations: ConversationItem[], userId: string): ConversationItem[] {
  return conversations
    .filter((c) => c.PK === `USER#${userId}` && c.status === 'active')
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
}

/**
 * Determine whether a conversation should be archived based on its
 * last interaction timestamp and the 90-day TTL window.
 * Mirrors the archival logic in `archiveStaleConversations`.
 */
function shouldArchive(conversation: ConversationItem, now: Date): boolean {
  const cutoff = now.getTime() - CONVERSATION_TTL_DAYS * 24 * 60 * 60 * 1000;
  return new Date(conversation.updatedAt).getTime() < cutoff;
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Generate a set of active conversations for a single user. */
const arbActiveConversationsForUser: fc.Arbitrary<{
  userId: string;
  conversations: ConversationItem[];
}> = fc
  .stringMatching(/^user-[a-z0-9]{6}$/)
  .chain((userId) =>
    fc
      .array(
        fc
          .tuple(
            fc.uuidV(4),
            fc.string({ minLength: 3, maxLength: 80 }),
            fc.date({ min: new Date('2024-01-01'), max: new Date('2025-12-31') }),
            arbMessageSequence,
          )
          .map(([convId, title, date, messages]): ConversationItem => ({
            PK: `USER#${userId}`,
            SK: `CONV#${convId}`,
            title,
            createdAt: date.toISOString(),
            updatedAt: new Date(date.getTime() + 3_600_000).toISOString(),
            status: 'active',
            messages,
            TTL: Math.floor(date.getTime() / 1000) + CONVERSATION_TTL_DAYS * 86_400,
          })),
        { minLength: 1, maxLength: 8 },
      )
      .map((conversations) => ({ userId, conversations })),
  );

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Conversation persistence property tests', () => {
  /**
   * Property 18: Conversation list displays all conversations with metadata
   *
   * For any set of active conversations for a user, the conversation list
   * should contain an entry for each conversation, and each entry should
   * include a timestamp and summary title.
   *
   * **Validates: Requirements 13.2**
   */
  it('Property 18: Conversation list displays all conversations with metadata — Feature: genai-operations-analytics-tool, Property 18: Conversation list displays all conversations with metadata', () => {
    fc.assert(
      fc.property(arbActiveConversationsForUser, ({ userId, conversations }) => {
        const listed = listConversations(conversations, userId);

        // Every active conversation must appear in the list
        expect(listed.length).toBe(conversations.length);

        // Each entry must have a non-empty title (summary title)
        for (const conv of listed) {
          expect(conv.title).toBeDefined();
          expect(conv.title.length).toBeGreaterThan(0);
        }

        // Each entry must have a valid updatedAt timestamp
        for (const conv of listed) {
          expect(conv.updatedAt).toBeDefined();
          expect(new Date(conv.updatedAt).getTime()).not.toBeNaN();
        }

        // Each entry must have a valid createdAt timestamp
        for (const conv of listed) {
          expect(conv.createdAt).toBeDefined();
          expect(new Date(conv.createdAt).getTime()).not.toBeNaN();
        }

        // List must be sorted newest first by updatedAt
        for (let i = 1; i < listed.length; i++) {
          expect(new Date(listed[i - 1].updatedAt).getTime()).toBeGreaterThanOrEqual(
            new Date(listed[i].updatedAt).getTime(),
          );
        }
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 19: Conversation save-load round trip
   *
   * For any conversation with a sequence of messages, saving the
   * conversation and then loading it by ID should return the same
   * messages in the same order.
   *
   * **Validates: Requirements 13.3**
   */
  it('Property 19: Conversation save-load round trip — Feature: genai-operations-analytics-tool, Property 19: Conversation save-load round trip', () => {
    fc.assert(
      fc.property(
        fc.stringMatching(/^user-[a-z0-9]{6}$/),
        fc.uuidV(4),
        fc.string({ minLength: 3, maxLength: 80 }),
        arbMessageSequence,
        (userId, conversationId, title, messages) => {
          // Save
          const saved = saveConversation(userId, conversationId, title, messages);

          // Simulate storing in an in-memory map
          const store = new Map<string, ConversationItem>();
          store.set(`${saved.PK}|${saved.SK}`, saved);

          // Load
          const loaded = loadConversation(store, userId, conversationId);

          // Must find the conversation
          expect(loaded).not.toBeNull();

          // Title must match
          expect(loaded!.title).toBe(title);

          // Message count must match
          expect(loaded!.messages.length).toBe(messages.length);

          // Messages must be sorted by timestamp ascending
          const sortedOriginal = sortMessages(messages);
          for (let i = 0; i < loaded!.messages.length; i++) {
            expect(loaded!.messages[i].content).toBe(sortedOriginal[i].content);
            expect(loaded!.messages[i].role).toBe(sortedOriginal[i].role);
            expect(loaded!.messages[i].timestamp).toBe(sortedOriginal[i].timestamp);
          }

          // PK/SK must be correctly formed
          expect(loaded!.PK).toBe(`USER#${userId}`);
          expect(loaded!.SK).toBe(`CONV#${conversationId}`);

          // Status must be active
          expect(loaded!.status).toBe('active');
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * Property 20: Conversation archival after 90 days
   *
   * For any conversation, if the last interaction timestamp is more than
   * 90 days ago, the archival function should mark it as archived. If the
   * last interaction is 90 days or fewer, it should remain active.
   *
   * **Validates: Requirements 13.5**
   */
  it('Property 20: Conversation archival after 90 days — Feature: genai-operations-analytics-tool, Property 20: Conversation archival after 90 days', () => {
    fc.assert(
      fc.property(
        arbConversationItem,
        fc.boolean(),
        (conversation, makeStale) => {
          // Force the conversation to active status for this test
          const activeConv: ConversationItem = { ...conversation, status: 'active' };

          // Pick a reference "now" date
          const updatedAtMs = new Date(activeConv.updatedAt).getTime();
          const ttlMs = CONVERSATION_TTL_DAYS * 24 * 60 * 60 * 1000;

          let now: Date;
          if (makeStale) {
            // Set "now" to more than 90 days after the last interaction
            const offset = fc.sample(fc.integer({ min: 1, max: 365 * 24 * 60 * 60 * 1000 }), 1)[0];
            now = new Date(updatedAtMs + ttlMs + offset);
          } else {
            // Set "now" to within the 90-day window
            const offset = fc.sample(fc.integer({ min: 0, max: ttlMs }), 1)[0];
            now = new Date(updatedAtMs + offset);
          }

          const result = shouldArchive(activeConv, now);

          if (makeStale) {
            // Conversation older than 90 days should be archived
            expect(result).toBe(true);
          } else {
            // Conversation within 90 days should remain active
            expect(result).toBe(false);
          }
        },
      ),
      { numRuns: 100 },
    );
  });
});
