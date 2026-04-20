/**
 * fast-check arbitraries for conversation and message objects.
 * Validates: Requirements 13.2
 */
import fc from 'fast-check';
import type { Message, ConversationItem } from '@shared/types';
import { DOMAINS, CONVERSATION_TTL_DAYS } from '@shared/constants';

/** ISO timestamp */
const arbTimestamp = fc
  .date({ min: new Date('2024-01-01'), max: new Date('2025-12-31') })
  .map((d) => d.toISOString());

/** Source domains used in message metadata */
const arbSourceDomains = fc.subarray([...DOMAINS], { minLength: 0, maxLength: DOMAINS.length });

/** A single conversation message */
export const arbMessage: fc.Arbitrary<Message> = fc.record({
  role: fc.constantFrom<'user' | 'assistant'>('user', 'assistant'),
  content: fc.string({ minLength: 1, maxLength: 500 }),
  timestamp: arbTimestamp,
  metadata: fc.option(
    fc.record({
      sourceDomains: arbSourceDomains,
      subAgentsUsed: arbSourceDomains,
    }),
    { nil: undefined },
  ),
});

/**
 * A sequence of messages that alternates user/assistant roles,
 * always starting with a user message.
 */
export const arbMessageSequence: fc.Arbitrary<Message[]> = fc
  .array(fc.string({ minLength: 1, maxLength: 300 }), { minLength: 1, maxLength: 10 })
  .chain((contents) => {
    // Generate timestamps in ascending order
    return fc
      .array(
        fc.date({ min: new Date('2024-01-01'), max: new Date('2025-12-31') }),
        { minLength: contents.length, maxLength: contents.length },
      )
      .map((dates) => {
        const sorted = dates.sort((a, b) => a.getTime() - b.getTime());
        return contents.map((content, i): Message => ({
          role: i % 2 === 0 ? 'user' : 'assistant',
          content,
          timestamp: sorted[i].toISOString(),
          metadata:
            i % 2 === 1
              ? { sourceDomains: ['cost'], subAgentsUsed: ['cost'] }
              : undefined,
        }));
      });
  });

/** Compute a TTL epoch (seconds) from a date, adding CONVERSATION_TTL_DAYS */
const ttlFromDate = (d: Date): number =>
  Math.floor(d.getTime() / 1000) + CONVERSATION_TTL_DAYS * 86_400;

/** A complete ConversationItem matching the DynamoDB schema */
export const arbConversationItem: fc.Arbitrary<ConversationItem> = fc
  .tuple(
    fc.stringMatching(/^user-[a-z0-9]{6}$/),
    fc.uuidV(4),
    fc.date({ min: new Date('2024-01-01'), max: new Date('2025-12-31') }),
  )
  .chain(([userId, convId, createdDate]) =>
    arbMessageSequence.chain((messages) =>
      fc.record({
        PK: fc.constant(`USER#${userId}`),
        SK: fc.constant(`CONV#${convId}`),
        title: fc.string({ minLength: 3, maxLength: 80 }),
        createdAt: fc.constant(createdDate.toISOString()),
        updatedAt: fc.constant(
          new Date(createdDate.getTime() + 3_600_000).toISOString(),
        ),
        status: fc.constantFrom<'active' | 'archived'>('active', 'archived'),
        messages: fc.constant(messages),
        TTL: fc.constant(ttlFromDate(createdDate)),
      }),
    ),
  );

/** A conversation with zero messages (edge case — newly created) */
export const arbEmptyConversation: fc.Arbitrary<ConversationItem> = fc
  .tuple(
    fc.stringMatching(/^user-[a-z0-9]{6}$/),
    fc.uuidV(4),
    fc.date({ min: new Date('2024-01-01'), max: new Date('2025-12-31') }),
  )
  .map(([userId, convId, createdDate]) => ({
    PK: `USER#${userId}`,
    SK: `CONV#${convId}`,
    title: 'New Conversation',
    createdAt: createdDate.toISOString(),
    updatedAt: createdDate.toISOString(),
    status: 'active' as const,
    messages: [],
    TTL: ttlFromDate(createdDate),
  }));
