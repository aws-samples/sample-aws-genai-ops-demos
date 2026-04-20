/**
 * G.O.A.T. – ConversationHistory component
 *
 * Lists previous conversations with timestamps and summary titles.
 * Allows loading and resuming a conversation.
 *
 * Validates: Requirements 13.2, 13.3
 */

import { useState, useEffect, useCallback } from 'react';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import type { ConversationItem } from '@shared/types';
import { listConversations, deleteConversation } from '../lib/dynamodb/conversations';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ConversationHistoryProps {
  userId: string;
  /** Called when the user selects a conversation to resume */
  onSelect: (conversationId: string) => void;
  /** Currently active conversation id (highlighted in the list) */
  activeConversationId?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function ConversationHistory({
  userId,
  onSelect,
  activeConversationId,
}: ConversationHistoryProps) {
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedItems, setSelectedItems] = useState<ConversationItem[]>([]);

  // ---- Fetch conversations on mount / userId change ----
  const refresh = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    try {
      const items = await listConversations(userId);
      setConversations(items);
    } catch {
      // Silently handle – table will show empty state
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // ---- Delete selected conversation ----
  const handleDelete = async () => {
    if (selectedItems.length === 0) return;
    const conv = selectedItems[0];
    const convId = conv.SK.replace('CONV#', '');
    await deleteConversation(userId, convId);
    setSelectedItems([]);
    refresh();
  };

  // ---- Format date for display ----
  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  return (
    <Table
      header={
        <Header
          variant="h3"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={refresh} loading={loading} />
              <Button
                disabled={selectedItems.length === 0}
                onClick={handleDelete}
              >
                Delete
              </Button>
            </SpaceBetween>
          }
        >
          Conversations
        </Header>
      }
      items={conversations}
      loading={loading}
      loadingText="Loading conversations…"
      selectionType="single"
      selectedItems={selectedItems}
      onSelectionChange={({ detail }) =>
        setSelectedItems(detail.selectedItems as ConversationItem[])
      }
      onRowClick={({ detail }) => {
        const convId = (detail.item as ConversationItem).SK.replace('CONV#', '');
        onSelect(convId);
      }}
      trackBy="SK"
      columnDefinitions={[
        {
          id: 'title',
          header: 'Title',
          cell: (item: ConversationItem) => (
            <Box fontWeight={item.SK.replace('CONV#', '') === activeConversationId ? 'bold' : 'normal'}>
              {item.title || 'Untitled'}
            </Box>
          ),
          sortingField: 'title',
        },
        {
          id: 'updatedAt',
          header: 'Last updated',
          cell: (item: ConversationItem) => formatDate(item.updatedAt),
          sortingField: 'updatedAt',
        },
        {
          id: 'messages',
          header: 'Messages',
          cell: (item: ConversationItem) => item.messages?.length ?? 0,
        },
      ]}
      empty={
        <Box textAlign="center" padding="l">
          <SpaceBetween size="m">
            <Box variant="p">No conversations yet.</Box>
            <Box variant="p" color="text-body-secondary">
              Start a chat to create your first conversation.
            </Box>
          </SpaceBetween>
        </Box>
      }
    />
  );
}
