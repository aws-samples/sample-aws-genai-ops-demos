import { useEffect, useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import Spinner from '@cloudscape-design/components/spinner';
import { getSample } from '../api';
import Report from '../components/report/Report';
import type { Report as R } from '../lib/types';

export default function SamplePage() {
  const [report, setReport] = useState<R | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    getSample<R>().then(setReport, (e) => setError((e as Error).message));
  }, []);
  return (
    <ContentLayout header={<Header variant="h1">Sample report</Header>}>
      {error ? <Alert type="error">{error}</Alert> : report ? <Report report={report} reportKey="sample" /> : <Spinner />}
    </ContentLayout>
  );
}
