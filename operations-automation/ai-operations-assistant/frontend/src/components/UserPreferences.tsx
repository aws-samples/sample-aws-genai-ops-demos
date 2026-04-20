/**
 * G.O.A.T. – UserPreferences component
 *
 * Modal-based settings panel for default account, preferred templates,
 * and display settings (theme, response format, chart type).
 *
 * Validates: Requirements 13.4
 */

import { useState, useEffect, useCallback } from 'react';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import Select, { SelectProps } from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Toggle from '@cloudscape-design/components/toggle';
import Alert from '@cloudscape-design/components/alert';
import {
  getPreferences,
  savePreferences,
  DEFAULT_PREFERENCES,
} from '../lib/dynamodb/user-preferences';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface UserPreferencesProps {
  userId: string;
  visible: boolean;
  onDismiss: () => void;
}

// ---------------------------------------------------------------------------
// Option helpers
// ---------------------------------------------------------------------------

const FORMAT_OPTIONS: SelectProps.Option[] = [
  { label: 'Detailed', value: 'detailed' },
  { label: 'Summary', value: 'summary' },
];

const CHART_OPTIONS: SelectProps.Option[] = [
  { label: 'Bar chart', value: 'bar' },
  { label: 'Line chart', value: 'line' },
];

function findOption(options: SelectProps.Option[], value: string) {
  return options.find((o) => o.value === value) ?? options[0];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function UserPreferences({
  userId,
  visible,
  onDismiss,
}: UserPreferencesProps) {
  const [defaultAccount, setDefaultAccount] = useState('');
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [responseFormat, setResponseFormat] = useState<'detailed' | 'summary'>('detailed');
  const [chartType, setChartType] = useState<'bar' | 'line'>('bar');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);

  // ---- Load preferences when modal opens ----
  const load = useCallback(async () => {
    if (!userId) return;
    try {
      const prefs = await getPreferences(userId);
      setDefaultAccount(prefs.defaultAccount ?? '');
      setTheme(prefs.displaySettings?.theme ?? 'light');
      setResponseFormat(prefs.displaySettings?.responseFormat ?? 'detailed');
      setChartType(prefs.displaySettings?.chartType ?? 'bar');
    } catch {
      // Use defaults on error
      setDefaultAccount(DEFAULT_PREFERENCES.defaultAccount);
      setTheme(DEFAULT_PREFERENCES.displaySettings.theme);
      setResponseFormat(DEFAULT_PREFERENCES.displaySettings.responseFormat);
      setChartType(DEFAULT_PREFERENCES.displaySettings.chartType);
    }
  }, [userId]);

  useEffect(() => {
    if (visible) {
      setError('');
      setSuccess(false);
      load();
    }
  }, [visible, load]);

  // ---- Save preferences ----
  const handleSave = async () => {
    setSaving(true);
    setError('');
    setSuccess(false);
    try {
      await savePreferences(userId, {
        defaultAccount,
        preferredTemplates: [],
        displaySettings: { theme, responseFormat, chartType },
      });
      setSuccess(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save preferences.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header={<Header variant="h2">User Preferences</Header>}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onDismiss}>
              Cancel
            </Button>
            <Button variant="primary" loading={saving} onClick={handleSave}>
              Save
            </Button>
          </SpaceBetween>
        </Box>
      }
      size="medium"
    >
      <SpaceBetween size="l">
        {error && <Alert type="error">{error}</Alert>}
        {success && <Alert type="success">Preferences saved.</Alert>}

        {/* Default account */}
        <FormField
          label="Default AWS account"
          description="Account ID used by default for cross-account queries."
        >
          <Input
            value={defaultAccount}
            placeholder="123456789012"
            onChange={({ detail }) => setDefaultAccount(detail.value)}
          />
        </FormField>

        {/* Theme toggle */}
        <FormField label="Theme">
          <Toggle
            checked={theme === 'dark'}
            onChange={({ detail }) => setTheme(detail.checked ? 'dark' : 'light')}
          >
            Dark mode
          </Toggle>
        </FormField>

        {/* Response format */}
        <FormField label="Response format">
          <Select
            selectedOption={findOption(FORMAT_OPTIONS, responseFormat)}
            options={FORMAT_OPTIONS}
            onChange={({ detail }) =>
              setResponseFormat(detail.selectedOption.value as 'detailed' | 'summary')
            }
          />
        </FormField>

        {/* Chart type */}
        <FormField label="Preferred chart type">
          <Select
            selectedOption={findOption(CHART_OPTIONS, chartType)}
            options={CHART_OPTIONS}
            onChange={({ detail }) =>
              setChartType(detail.selectedOption.value as 'bar' | 'line')
            }
          />
        </FormField>
      </SpaceBetween>
    </Modal>
  );
}
