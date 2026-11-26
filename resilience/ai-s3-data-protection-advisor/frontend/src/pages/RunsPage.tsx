import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Flashbar, { FlashbarProps } from '@cloudscape-design/components/flashbar';
import Header from '@cloudscape-design/components/header';
import Link from '@cloudscape-design/components/link';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import { ApiError, listRuns, Run, startScan } from '../api';
import { formatDate } from '../lib/format';
import { isActive, recsLabel, runStatus } from '../lib/runs';

export default function RunsPage() {
  const navigate = useNavigate();
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const [notes, setNotes] = useState<FlashbarProps.MessageDefinition[]>([]);

  const load = useCallback(async () => {
    try {
      setRuns((await listRuns()).runs);
      setError('');
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const running = runs?.some(isActive) ?? false;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => void load(), 15000);
    return () => clearInterval(t);
  }, [running, load]);

  const start = async () => {
    setStarting(true);
    try {
      const { run_id } = await startScan();
      navigate(`/runs/${run_id}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const id = e.runId;
        setNotes([{
          type: 'info', dismissible: true, onDismiss: () => setNotes([]),
          content: (
            <>A scan is already running.{id && <> <Link href={`/runs/${id}`} onFollow={(ev) => { ev.preventDefault(); navigate(`/runs/${id}`); }}>Open the running scan</Link></>}</>
          ),
        }]);
      } else {
        setNotes([{ type: 'error', dismissible: true, onDismiss: () => setNotes([]), content: (e as Error).message }]);
      }
    } finally {
      setStarting(false);
    }
  };

  const startButton = <Button variant="primary" loading={starting} onClick={() => void start()}>Start scan</Button>;

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Use the sample report to explore the dashboard with sample data from a fictitious estate."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button href="/sample" onFollow={(e) => { e.preventDefault(); navigate('/sample'); }}
                ariaLabel="View sample report: sample data, fictitious estate">View sample report</Button>
              {startButton}
            </SpaceBetween>
          }
        >
          Runs
        </Header>
      }
    >
      <SpaceBetween size="m">
        <Flashbar items={notes} />
        <Table
          loading={runs === null && !error}
          loadingText="Loading runs"
          items={runs ?? []}
          trackBy="run_id"
          variant="container"
          resizableColumns
          columnDefinitions={[
            { id: 'id', header: 'Run', width: 220, minWidth: 140, cell: (r) => <Link href={`/runs/${r.run_id}`} onFollow={(e) => { e.preventDefault(); navigate(`/runs/${r.run_id}`); }}>{r.run_id}</Link> },
            { id: 'status', header: 'Status', width: 130, minWidth: 110, cell: (r) => { const s = runStatus(r); return <StatusIndicator type={s.type}>{s.label}</StatusIndicator>; } },
            { id: 'started', header: 'Started', width: 170, minWidth: 150, cell: (r) => formatDate(r.started_at) },
            { id: 'scope', header: 'Scope', width: 160, minWidth: 100, cell: (r) => r.scope ?? '-' },
            { id: 'buckets', header: 'Buckets', width: 90, minWidth: 70, cell: (r) => r.buckets ?? '-' },
            { id: 'accounts', header: 'Accounts', width: 100, minWidth: 80, cell: (r) => r.accounts?.length ?? '-' },
            { id: 'recs', header: 'Recommendations (H / M / L)', width: 200, minWidth: 140, cell: (r) => recsLabel(r.recs) },
          ]}
          empty={
            error ? (
              <Box textAlign="center" color="text-status-error">{error}</Box>
            ) : (
              <Box textAlign="center" padding="m">
                <SpaceBetween size="m">
                  <b>No scans yet</b>
                  {startButton}
                </SpaceBetween>
              </Box>
            )
          }
        />
      </SpaceBetween>
    </ContentLayout>
  );
}
