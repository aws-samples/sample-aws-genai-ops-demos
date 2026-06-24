/**
 * G.O.A.T. – AccountSelector component
 *
 * Cross-account target selection using Cloudscape Select + FormField.
 * Optional — only relevant when cross-account access is configured.
 *
 * Validates: Requirements 12.2 (optional)
 */

import { useState } from 'react';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Select, { SelectProps } from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface AccountSelectorProps {
  /** Currently selected account ID (empty string = current account) */
  selectedAccountId: string;
  /** Called when the user picks a different account */
  onChange: (accountId: string) => void;
  /** Pre-configured account list (id → label). If empty, shows a free-text input. */
  accounts?: { id: string; label: string }[];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const CURRENT_ACCOUNT_OPTION: SelectProps.Option = {
  label: 'Current account',
  value: '',
};

export default function AccountSelector({
  selectedAccountId,
  onChange,
  accounts = [],
}: AccountSelectorProps) {
  const [customId, setCustomId] = useState(selectedAccountId); // nosemgrep: react-props-in-state — initial value only, not sync'd

  // Build dropdown options from the provided account list
  const options: SelectProps.Option[] = [
    CURRENT_ACCOUNT_OPTION,
    ...accounts.map((a) => ({ label: `${a.label} (${a.id})`, value: a.id })),
  ];

  const selectedOption =
    options.find((o) => o.value === selectedAccountId) ?? CURRENT_ACCOUNT_OPTION;

  // If accounts are provided, use a Select dropdown; otherwise free-text input
  if (accounts.length > 0) {
    return (
      <FormField
        label="Target account"
        description="Select an AWS account for cross-account queries."
      >
        <Select
          selectedOption={selectedOption}
          options={options}
          onChange={({ detail }) => onChange(detail.selectedOption.value ?? '')}
          placeholder="Choose an account"
        />
      </FormField>
    );
  }

  // Free-text fallback
  return (
    <FormField
      label="Target account"
      description="Enter an AWS account ID for cross-account queries, or leave blank for the current account."
    >
      <SpaceBetween direction="horizontal" size="xs">
        <Input
          value={customId}
          placeholder="123456789012"
          onChange={({ detail }) => setCustomId(detail.value)}
          onBlur={() => onChange(customId)}
        />
      </SpaceBetween>
    </FormField>
  );
}
