// Activity log page: the recent scan (durable pipeline) executions and
// migration batches as one Cloudscape gen-AI Event log timeline. A left-nav page
// (not a split-panel drawer) so it is a first-class view. The timeline itself
// is the reusable ScanLog component. The route stays /scan-log.
import ContentLayout from '@cloudscape-design/components/content-layout';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import ScanLog from '../components/ScanLog';

export default function ScanLogPage() {
  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="The most recent organization-wide scans and migration batches, interleaved newest-first — what each scan discovered, assessed, or skipped, and how each migration batch ended."
        >
          Activity log
        </Header>
      }
    >
      <Container>
        <ScanLog />
      </Container>
    </ContentLayout>
  );
}
