import Alert from '@cloudscape-design/components/alert';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Tabs from '@cloudscape-design/components/tabs';
import { reportNotice } from '../../lib/status';
import { tabList } from '../../lib/tabs';
import type * as Ty from '../../lib/types';
import BucketsTab from './BucketsTab';
import CostTab from './CostTab';
import Downloads from './Downloads';
import GenericTab from './GenericTab';
import GovernanceTab from './GovernanceTab';
import InconsistenciesTab from './InconsistenciesTab';
import InstructionsTab from './InstructionsTab';
import RecommendationsTab from './RecommendationsTab';
import RiskReviewTab from './RiskReviewTab';

interface Props {
  report: Ty.Report;
  /** Run id, or "sample". Used by the Buckets tab (Task 5) for the exclusion filter. */
  reportKey: string;
  downloads?: boolean;
}

export default function Report({ report, reportKey, downloads }: Props) {
  const entries = tabList(report?.tabs);
  return (
    <SpaceBetween size="m">
      <Alert type="info">{reportNotice(report?.meta)}</Alert>
      {report?.meta?.sample && <Alert type="info">Sample data, fictitious estate.</Alert>}
      {downloads && <Downloads runId={reportKey} />}
      <Tabs
        tabs={entries.map((e) => ({
          id: e.id,
          label: e.label,
          content: (() => {
            switch (e.kind) {
              case 'instructions': return <InstructionsTab tab={e.tab as Ty.InstructionsTab} />;
              case 'cards': return <BucketsTab tab={e.tab as Ty.CardsTab} reportKey={reportKey} />;
              case 'operational-review': return <RiskReviewTab tab={e.tab as Ty.OperationalReviewTab} />;
              case 'recommendations': return <RecommendationsTab tab={e.tab as Ty.RecommendationsTab} />;
              case 'inconsistencies': return <InconsistenciesTab tab={e.tab as Ty.InconsistenciesTab} />;
              case 'governance': return <GovernanceTab tab={e.tab as Ty.GovernanceTab} />;
              case 'cost-optimization': return <CostTab tab={e.tab as Ty.CostOptimizationTab} />;
              default: return <GenericTab tab={e.tab} />;
            }
          })(),
        }))}
      />
    </SpaceBetween>
  );
}
