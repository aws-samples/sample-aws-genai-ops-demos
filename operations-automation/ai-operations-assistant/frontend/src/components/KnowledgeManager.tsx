/**
 * G.O.A.T. – KnowledgeManager component
 *
 * Provides a UI for creating, searching, and exporting knowledge articles.
 * Uses Cloudscape Table, Modal, FormField, and the DynamoDB knowledge-articles
 * data access layer.
 *
 * Validates: Requirements 9.1, 9.3, 9.4, 9.5
 */

import { useState, useEffect, useCallback } from 'react';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import Textarea from '@cloudscape-design/components/textarea';
import Alert from '@cloudscape-design/components/alert';
import type { KnowledgeArticleItem } from '@shared/types';
import {
  createArticle,
  searchArticles,
  buildWebhookPayload,
} from '../lib/dynamodb/knowledge-articles';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CATEGORIES = [
  { label: 'Health', value: 'health' },
  { label: 'Cost', value: 'cost' },
  { label: 'Support', value: 'support' },
  { label: 'Trusted Advisor', value: 'trusted_advisor' },
  { label: 'CUR', value: 'cur' },
  { label: 'General', value: 'general' },
];

const WEBHOOK_URL_KEY = 'goat_webhook_url';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function KnowledgeManager() {
  // Article list state
  const [articles, setArticles] = useState<KnowledgeArticleItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  // Create modal state
  const [showCreate, setShowCreate] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newCategory, setNewCategory] = useState(CATEGORIES[0]);
  const [newTags, setNewTags] = useState('');
  const [newContent, setNewContent] = useState('');
  const [saving, setSaving] = useState(false);

  // Export state
  const [webhookUrl, setWebhookUrl] = useState(
    () => localStorage.getItem(WEBHOOK_URL_KEY) ?? '',
  );
  const [exportStatus, setExportStatus] = useState<{
    type: 'success' | 'error';
    message: string;
  } | null>(null);

  // ------------------------------------------------------------------
  // Search / load articles
  // ------------------------------------------------------------------

  const loadArticles = useCallback(async (query: string) => {
    setLoading(true);
    try {
      const results = await searchArticles(query || '');
      setArticles(results);
    } catch {
      // DynamoDB may not be configured in dev — show empty list
      setArticles([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadArticles('');
  }, [loadArticles]);

  const handleSearch = () => {
    loadArticles(searchQuery);
  };

  // ------------------------------------------------------------------
  // Create article
  // ------------------------------------------------------------------

  const resetCreateForm = () => {
    setNewTitle('');
    setNewCategory(CATEGORIES[0]);
    setNewTags('');
    setNewContent('');
  };

  const handleCreate = async () => {
    setSaving(true);
    try {
      const articleId = crypto.randomUUID();
      await createArticle({
        articleId,
        title: newTitle.trim(),
        category: newCategory.value,
        tags: newTags
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        content: newContent,
        sourceAgents: [],
        originalQuery: '',
        createdAt: new Date().toISOString(),
        createdBy: 'user',
      });
      setShowCreate(false);
      resetCreateForm();
      loadArticles(searchQuery);
    } catch {
      // Silently handle — DynamoDB may not be available in dev
    } finally {
      setSaving(false);
    }
  };

  // ------------------------------------------------------------------
  // Export article via webhook
  // ------------------------------------------------------------------

  const handleExport = async (article: KnowledgeArticleItem) => {
    if (!webhookUrl.trim()) {
      setExportStatus({ type: 'error', message: 'Set a webhook URL first (Settings row above the table).' });
      return;
    }

    const payload = buildWebhookPayload(article);

    try {
      const response = await fetch(webhookUrl.trim(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      setExportStatus({ type: 'success', message: `Exported "${article.title}" successfully.` });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Export failed';
      setExportStatus({ type: 'error', message: `Export failed: ${msg}` });
    }
  };

  // Persist webhook URL
  const handleWebhookUrlChange = (value: string) => {
    setWebhookUrl(value);
    localStorage.setItem(WEBHOOK_URL_KEY, value);
  };

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Save, search, and export knowledge articles created from query findings."
        actions={
          <Button variant="primary" onClick={() => setShowCreate(true)}>
            Create article
          </Button>
        }
      >
        Knowledge Base
      </Header>

      {exportStatus && (
        <Alert
          type={exportStatus.type}
          dismissible
          onDismiss={() => setExportStatus(null)}
        >
          {exportStatus.message}
        </Alert>
      )}

      {/* Webhook URL setting */}
      <FormField label="Webhook URL" description="Endpoint for exporting articles as JSON (e.g. Confluence, ServiceNow).">
        <Input
          value={webhookUrl}
          onChange={({ detail }) => handleWebhookUrlChange(detail.value)}
          placeholder="https://example.com/webhook"
          type="url"
        />
      </FormField>

      {/* Search bar */}
      <SpaceBetween size="xs" direction="horizontal">
        <Input
          value={searchQuery}
          onChange={({ detail }) => setSearchQuery(detail.value)}
          placeholder="Search articles by keyword…"
          type="search"
        />
        <Button onClick={handleSearch}>Search</Button>
      </SpaceBetween>

      {/* Articles table */}
      <Table
        items={articles}
        loading={loading}
        loadingText="Loading articles…"
        columnDefinitions={[
          {
            id: 'title',
            header: 'Title',
            cell: (item) => item.title,
            sortingField: 'title',
          },
          {
            id: 'category',
            header: 'Category',
            cell: (item) => item.category,
            sortingField: 'category',
          },
          {
            id: 'tags',
            header: 'Tags',
            cell: (item) => item.tags.join(', '),
          },
          {
            id: 'createdAt',
            header: 'Created',
            cell: (item) =>
              new Date(item.createdAt).toLocaleDateString(),
            sortingField: 'createdAt',
          },
          {
            id: 'actions',
            header: 'Actions',
            cell: (item) => (
              <Button
                variant="inline-link"
                onClick={() => handleExport(item)}
              >
                Export
              </Button>
            ),
          },
        ]}
        empty={
          <Box textAlign="center" color="text-body-secondary" padding="l">
            No knowledge articles found. Create one or adjust your search.
          </Box>
        }
        header={
          <Header counter={`(${articles.length})`}>Articles</Header>
        }
      />

      {/* Create article modal */}
      <Modal
        visible={showCreate}
        onDismiss={() => setShowCreate(false)}
        header="Create Knowledge Article"
        footer={
          <Box float="right">
            <SpaceBetween size="xs" direction="horizontal">
              <Button variant="link" onClick={() => setShowCreate(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={saving}
                disabled={!newTitle.trim() || !newContent.trim()}
                onClick={handleCreate}
              >
                Save article
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Title">
            <Input
              value={newTitle}
              onChange={({ detail }) => setNewTitle(detail.value)}
              placeholder="Article title"
            />
          </FormField>

          <FormField label="Category">
            <Select
              selectedOption={newCategory}
              onChange={({ detail }) =>
                setNewCategory(
                  detail.selectedOption as { label: string; value: string },
                )
              }
              options={CATEGORIES}
            />
          </FormField>

          <FormField label="Tags" description="Comma-separated list of tags.">
            <Input
              value={newTags}
              onChange={({ detail }) => setNewTags(detail.value)}
              placeholder="e.g. ec2, cost, outage"
            />
          </FormField>

          <FormField label="Content">
            <Textarea
              value={newContent}
              onChange={({ detail }) => setNewContent(detail.value)}
              placeholder="Article content…"
              rows={6}
            />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
}
