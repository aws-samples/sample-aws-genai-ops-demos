import { useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import { reportUrl } from '../../api';

export default function Downloads({ runId }: { runId: string }) {
  const [busy, setBusy] = useState<'xlsx' | 'html' | null>(null);
  const [error, setError] = useState('');

  const go = async (format: 'xlsx' | 'html') => {
    setBusy(format);
    setError('');
    try {
      const { url } = await reportUrl(runId, format);
      window.location.assign(url);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <SpaceBetween size="s">
      {error && <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>}
      <SpaceBetween direction="horizontal" size="xs">
        <Button iconName="download" loading={busy === 'xlsx'} disabled={busy !== null} onClick={() => void go('xlsx')}>Download Excel</Button>
        <Button iconName="download" loading={busy === 'html'} disabled={busy !== null} onClick={() => void go('html')}>Download HTML</Button>
      </SpaceBetween>
    </SpaceBetween>
  );
}
