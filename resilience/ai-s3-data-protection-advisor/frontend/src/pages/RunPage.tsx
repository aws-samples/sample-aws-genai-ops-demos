import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import Alert from '@cloudscape-design/components/alert';
import ContentLayout from '@cloudscape-design/components/content-layout';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Spinner from '@cloudscape-design/components/spinner';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { fetchReport, getRun, reportUrl, RunDetail } from '../api';
import ReportView from '../components/report/Report';
import type { Report } from '../lib/types';
import RunSteps from '../components/RunSteps';
import { accountLabel, formatDate, formatDuration } from '../lib/format';
import { isActive, runStatus } from '../lib/runs';

function RunReport({ runId }: { runId: string }) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    setReport(null);
    setError('');
    reportUrl(runId, 'json')
      .then(({ url }) => fetchReport<Report>(url))
      .then(
        (r) => { if (!cancelled) setReport(r); },
        (e) => { if (!cancelled) setError((e as Error).message); },
      );
    return () => { cancelled = true; };
  }, [runId]);
  if (error) return <Alert type="error">{error}</Alert>;
  if (!report) return <Spinner />;
  return <ReportView report={report} reportKey={runId} downloads />;
}

export default function RunPage() {
  const { id = '' } = useParams();
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async (isCurrent: () => boolean = () => true) => {
    try {
      const d = await getRun(id);
      if (isCurrent()) { setDetail(d); setError(''); }
    } catch (e) {
      if (isCurrent()) setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    let current = true;
    setDetail(null);
    void load(() => current);
    return () => { current = false; };
  }, [load]);

  const active = isActive(detail?.run);
  useEffect(() => {
    if (!active) return;
    let current = true;
    const t = setInterval(() => void load(() => current), 10000);
    return () => { current = false; clearInterval(t); };
  }, [active, load]);

  if (!detail) {
    return (
      <ContentLayout header={<Header variant="h1">{id}</Header>}>
        {error ? <Alert type="error">{error}</Alert> : <Spinner />}
      </ContentLayout>
    );
  }

  const { run, steps } = detail;
  const st = runStatus(run);
  const stepsView = <RunSteps steps={steps} />;

  return (
    <ContentLayout
      header={<Header variant="h1" description={<StatusIndicator type={st.type}>{st.label}</StatusIndicator>}>{run.run_id}</Header>}
    >
      <SpaceBetween size="l">
        {error && <Alert type="warning">{error}</Alert>}
        {run.status === 'FAILED' && (
          <Alert type="error">
            The scan failed ({run.error ?? 'no detail available'}). Start a new scan; collected data is kept for 14 days.
          </Alert>
        )}
        <KeyValuePairs
          columns={3}
          items={[
            { label: 'Started', value: formatDate(run.started_at) },
            { label: 'Duration', value: formatDuration(run.started_at, run.finished_at) || '-' },
            { label: 'Scope', value: run.scope ?? '-' },
          ]}
        />
        {run.status === 'SUCCEEDED' ? (
          <ExpandableSection headerText="Scan steps" variant="container">{stepsView}</ExpandableSection>
        ) : (
          stepsView
        )}
        {run.accounts && run.accounts.length > 0 && (
          <SpaceBetween size="s">
            <Header variant="h2">Accounts</Header>
            <KeyValuePairs
              columns={3}
              items={run.accounts.map((a) => ({ label: accountLabel(a.name, a.id), value: a.outcome }))}
            />
          </SpaceBetween>
        )}
        {run.status === 'SUCCEEDED' && <RunReport runId={run.run_id} />}
      </SpaceBetween>
    </ContentLayout>
  );
}
