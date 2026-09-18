// Tag filter form (#164), after the Resource Groups Tag Editor console ("Find
// resources to tag"): a tag key, an optional tag value, Add, the chosen pairs
// as tokens, Apply. Keys and values are suggested from what the last scan saw.
import { useEffect, useMemo, useState } from 'react';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import FormField from '@cloudscape-design/components/form-field';
import Autosuggest from '@cloudscape-design/components/autosuggest';
import TokenGroup from '@cloudscape-design/components/token-group';
import Grid from '@cloudscape-design/components/grid';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { getLifecycleData, DeprecationItem } from '../api';
import { TagFilter, addFilter, tokenText, tagKeys, tagValues, NOT_TAGGED, resourceTotal, scopeInventory } from '../tag-filter';

interface Props {
  visible: boolean;
  initial: TagFilter[];
  onDismiss: () => void;
  onApply: (filters: TagFilter[]) => void;
}

export default function TagFilterModal({ visible, initial, onDismiss, onApply }: Props) {
  const [rows, setRows] = useState<DeprecationItem[] | null>(null);
  const [error, setError] = useState('');
  const [filters, setFilters] = useState<TagFilter[]>(initial);
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');

  useEffect(() => { if (visible) { setFilters(initial); setKey(''); setValue(''); } }, [visible, initial]);
  useEffect(() => {
    if (!visible || rows) return;
    getLifecycleData().then((d) => setRows(d.inventoryAll)).catch((e) => setError(`Tag keys could not be retrieved: ${e.message}`));
  }, [visible, rows]);

  const keys = useMemo(() => (rows ? tagKeys(rows) : []), [rows]);
  const values = useMemo(() => (rows && key ? tagValues(rows, key) : []), [rows, key]);
  const total = useMemo(() => (rows ? resourceTotal(rows) : 0), [rows]);
  const preview = useMemo(() => (rows && filters.length ? scopeInventory(rows, filters).stats : null), [rows, filters]);

  const add = () => {
    const k = key.trim();
    if (!k) return;
    setFilters(addFilter(filters, k, value.trim()));
    setKey(''); setValue('');
  };

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      size="large"
      header="Tag filter"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onDismiss}>Cancel</Button>
            {initial.length > 0 && <Button onClick={() => onApply([])}>Clear filter</Button>}
            <Button variant="primary" onClick={() => onApply(filters)} disabled={!filters.length && !initial.length}>Apply</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        <Box color="text-body-secondary">
          Show only the resources carrying these tags, everywhere in the tracker. Several values of one key match any of them; several keys must all match.
          A key without value means "tagged with this key"; {NOT_TAGGED} selects the resources without it.
        </Box>
        {error && <StatusIndicator type="error">{error}</StatusIndicator>}
        <Grid gridDefinition={[{ colspan: 5 }, { colspan: 5 }, { colspan: 2 }]}>
          <FormField label="Tag key" description={rows ? `${keys.length} keys seen on ${total} resources` : undefined}>
            <Autosuggest
              value={key}
              onChange={({ detail }) => { setKey(detail.value); setValue(''); }}
              options={keys.map((k) => ({ value: k.key, description: `${k.resources} resource${k.resources === 1 ? '' : 's'}, ${k.distinct} value${k.distinct === 1 ? '' : 's'}` }))}
              placeholder="Tag key"
              statusType={rows || error ? 'finished' : 'loading'}
              loadingText="Loading tag keys"
              empty="No tag seen by the last scan"
              enteredTextLabel={(v) => `Use "${v}"`}
              ariaLabel="Tag key"
              onKeyDown={(e) => { if (e.detail.key === 'Enter') add(); }}
            />
          </FormField>
          <FormField label={<span>Tag value <i>- optional</i></span>}>
            <Autosuggest
              value={value}
              onChange={({ detail }) => setValue(detail.value)}
              options={values.map((v) => ({ value: v.value, description: `${v.resources} resource${v.resources === 1 ? '' : 's'}` }))}
              placeholder="Tag value"
              disabled={!key.trim()}
              empty={key ? 'No value seen for this key' : 'Choose a key first'}
              enteredTextLabel={(v) => `Use "${v}"`}
              ariaLabel="Tag value"
              onKeyDown={(e) => { if (e.detail.key === 'Enter') add(); }}
            />
          </FormField>
          <FormField label={<span>&nbsp;</span>}>
            <Button onClick={add} disabled={!key.trim()}>Add</Button>
          </FormField>
        </Grid>
        <TokenGroup
          items={filters.map((f) => ({ label: tokenText(f), dismissLabel: `Remove ${tokenText(f)}` }))}
          onDismiss={({ detail }) => setFilters(filters.filter((_, i) => i !== detail.itemIndex))}
          alignment="horizontal"
        />
        {preview && (
          <Box variant="small" color="text-body-secondary">
            {preview.matched} of {preview.total} resources match ({preview.rowsMatched} of {preview.rowsTotal} versions).
          </Box>
        )}
      </SpaceBetween>
    </Modal>
  );
}
