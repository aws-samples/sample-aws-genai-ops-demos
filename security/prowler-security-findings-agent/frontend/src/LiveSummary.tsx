import { useEffect, useState } from 'react';
import {
  Container,
  ExpandableSection,
  Header,
  Link,
  SpaceBetween,
  StatusIndicator,
} from '@cloudscape-design/components';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { LiveSummary } from './api';
import type { RunState } from './investigation-status';

function updatedAgo(iso: string | undefined, now: number): string {
  const then = iso ? new Date(iso).getTime() : NaN;
  if (Number.isNaN(then)) return '';
  const m = Math.floor(Math.max(0, now - then) / 60000);
  if (m < 1) return 'Updated just now';
  if (m < 60) return `Updated ${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `Updated ${h} h ago`;
  const d = Math.floor(h / 24);
  return d === 1 ? 'Updated yesterday' : `Updated ${d} days ago`;
}

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

function SummaryStatus({ summary, state }: { summary: LiveSummary; state: RunState }) {
  if (summary.final) return <StatusIndicator type="success">Final summary</StatusIndicator>;
  if (state === 'running') return <StatusIndicator type="in-progress">In progress</StatusIndicator>;
  if (state === 'failed') return <StatusIndicator type="error">Investigation failed</StatusIndicator>;
  return <StatusIndicator type="stopped">Stopped</StatusIndicator>;
}

function Updated({ iso, source }: { iso: string; source: string }) {
  const now = useNow(30000);
  const ago = updatedAgo(iso, now);
  if (!ago) return <>{source}</>;
  return <span title={new Date(iso).toLocaleString()}>{`${source} · ${ago}`}</span>;
}

function SummaryBody({ content }: { content: string }) {
  return (
    <div className="soc-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

/** The DevOps Agent summary of one investigation, for the finding detail page. */
export function LiveSummaryPanel({ summary, state, embedded = false }: { summary: LiveSummary; state: RunState; embedded?: boolean }) {
  if (embedded) {
    return (
      <SpaceBetween size="xs">
        <Header
          variant="h3"
          description={<Updated iso={summary.updatedAt} source="AWS DevOps Agent" />}
          actions={<SummaryStatus summary={summary} state={state} />}
        >
          Live summary
        </Header>
        <SummaryBody content={summary.content} />
      </SpaceBetween>
    );
  }
  return (
    <Container
      header={
        <Header
          variant="h2"
          description={<Updated iso={summary.updatedAt} source="AWS DevOps Agent" />}
          actions={<SummaryStatus summary={summary} state={state} />}
        >
          Live summary
        </Header>
      }
    >
      <SummaryBody content={summary.content} />
    </Container>
  );
}

export interface LiveSummaryItem {
  id: string;
  heading: string;
  href: string;
  summary: LiveSummary;
  state: RunState;
}

/** Every investigation that has a summary, for the Investigations page. Running ones start expanded. */
export function LiveSummaryList({ items, onFollow }: { items: LiveSummaryItem[]; onFollow: (href: string) => void }) {
  const now = useNow(30000);
  return (
    <Container
      header={
        <Header
          variant="h2"
          counter={`(${items.length})`}
          description="The latest AWS DevOps Agent summary for each investigation. Refreshes every 10 s while one is running."
        >
          Live summaries
        </Header>
      }
    >
      <SpaceBetween size="s">
        {items.map((it) => (
          <ExpandableSection
            key={it.id}
            defaultExpanded={it.state === 'running' && !it.summary.final}
            headerText={it.heading}
            headerDescription={updatedAgo(it.summary.updatedAt, now) || undefined}
            headerActions={
              <SpaceBetween direction="horizontal" size="s" alignItems="center">
                <SummaryStatus summary={it.summary} state={it.state} />
                <Link
                  href={it.href}
                  onFollow={(e) => {
                    e.preventDefault();
                    onFollow(it.href);
                  }}
                >
                  Open finding
                </Link>
              </SpaceBetween>
            }
          >
            <SummaryBody content={it.summary.content} />
          </ExpandableSection>
        ))}
      </SpaceBetween>
    </Container>
  );
}
