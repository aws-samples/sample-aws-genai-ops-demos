// Settings — configure how the tool reaches your Git platform and how it
// detects each Lambda function's source repository before the migration phase
// can push an upgrade branch to it.
//
// Nothing on this page triggers a migration: it only CONFIGURES Git access and
// the detection method.
//
// Two concerns, two Containers:
//   1. Git platform access — where the upgrade branch is pushed, and a Secrets
//      Manager reference for the token (the token value itself is never captured).
//   2. Code-behind mapping detection — a Tiles selector: Tag-based (today) vs
//      AI-driven (roadmap, disabled).
//
// Only non-secret fields are persisted, via the existing /actions dispatcher
// (get_settings / put_settings) into the existing table under a settings# key.
// Modeled on StatisticsPage: ContentLayout + Header, load on mount, Cloudscape
// loading / error (+Retry) conventions.
import { useState, useEffect, useCallback, useRef } from 'react';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Form from '@cloudscape-design/components/form';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Select, { SelectProps } from '@cloudscape-design/components/select';
import Tiles from '@cloudscape-design/components/tiles';
import Button from '@cloudscape-design/components/button';
import Box from '@cloudscape-design/components/box';
import Icon from '@cloudscape-design/components/icon';
import Alert from '@cloudscape-design/components/alert';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import PromptInput from '@cloudscape-design/components/prompt-input';
import {
  getSettings,
  saveSettings,
  resolveReposDemo,
  GitPlatform,
  DetectionMethod,
  RepoResolution,
} from '../api';

const DEFAULT_REPO_TAG = 'source-repo';

// Sample support prompts (the chips under the playground input). Clicking one
// fills the prompt — the operator describes how their repositories are
// organized so the resolver has something to reason from.
const SAMPLE_PROMPTS: string[] = [
  'Repos named after the service',
  'CloudFormation stack tag = repo',
  'CMDB id maps via registry',
];

// The four progressive step labels shown while the single backend call is in
// flight. The stepper advances client-side on a timer; it does not reflect real
// backend streaming.
const STEP_LABELS: string[] = [
  'Pick functions from the inventory',
  "Read each function's tags, name, and runtime",
  'Search the Git organization for candidates',
  'Score each candidate and attach evidence',
];

// Confidence tiers (from the mock's tier() mapping).
type Tier = { key: 'high' | 'med' | 'low'; label: string; indicator: 'success' | 'warning' | 'error' };
function confidenceTier(confidence: number): Tier {
  if (confidence >= 0.8) return { key: 'high', label: 'High confidence', indicator: 'success' };
  if (confidence >= 0.5) return { key: 'med', label: 'Needs review', indicator: 'warning' };
  return { key: 'low', label: 'Unresolved', indicator: 'error' };
}

const PLATFORM_OPTIONS: SelectProps.Option[] = [
  { value: 'github', label: 'GitHub' },
  { value: 'gitlab', label: 'GitLab' },
  { value: 'bitbucket', label: 'Bitbucket' },
  { value: 'codecommit', label: 'AWS CodeCommit' },
];

export default function SettingsPage() {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Form state (non-secret only).
  const [platform, setPlatform] = useState<GitPlatform>('github');
  const [host, setHost] = useState('');
  const [org, setOrg] = useState('');
  const [secretRef, setSecretRef] = useState('');
  const [detectionMethod, setDetectionMethod] = useState<DetectionMethod>('tag');
  // Tag that names the repository; honor a legacy repoTagKey when loading.
  const [repoTag, setRepoTag] = useState(DEFAULT_REPO_TAG);
  // Optional tag that names the release branch; empty = default branch.
  const [branchTag, setBranchTag] = useState('');

  // AI detection playground state (read-only; nothing here is persisted).
  const [aiPrompt, setAiPrompt] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiResults, setAiResults] = useState<RepoResolution[] | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const stepTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearStepTimer = () => {
    if (stepTimer.current !== null) {
      clearInterval(stepTimer.current);
      stepTimer.current = null;
    }
  };

  // Clear the stepper interval on unmount.
  useEffect(() => clearStepTimer, []);

  const runPlayground = async () => {
    const prompt = aiPrompt.trim();
    if (!prompt || aiLoading) return;
    setAiLoading(true);
    setAiError(null);
    setAiResults(null);
    setStepIndex(0);
    // Advance the stepper client-side while the single backend call is in
    // flight (~850ms per step, matching the mock). This does not reflect real
    // backend streaming.
    clearStepTimer();
    stepTimer.current = setInterval(() => {
      setStepIndex((i) => (i < STEP_LABELS.length - 1 ? i + 1 : i));
    }, 850);
    try {
      const { results } = await resolveReposDemo(prompt);
      setAiResults(results);
    } catch (err: any) {
      setAiError(err?.message || "The source repositories couldn't be resolved. Try again.");
    } finally {
      clearStepTimer();
      setAiLoading(false);
    }
  };

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setLoadError(null);
      const { settings } = await getSettings();
      if (settings.platform) setPlatform(settings.platform);
      setHost(settings.host || '');
      setOrg(settings.org || '');
      setSecretRef(settings.secretRef || '');
      // Restore the stored detection method; anything other than 'ai' is 'tag'.
      setDetectionMethod(settings.detectionMethod === 'ai' ? 'ai' : 'tag');
      // Honor the backend backfill and a legacy value directly.
      setRepoTag(settings.repoTag || settings.repoTagKey || DEFAULT_REPO_TAG);
      setBranchTag(settings.branchTag || '');
    } catch (err: any) {
      setLoadError(err?.message || "The settings couldn't be fetched. Try again later.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const isCodeCommit = platform === 'codecommit';
  const orgRequired = !isCodeCommit;
  const orgMissing = orgRequired && !org.trim();

  const handleSave = async () => {
    setSaved(false);
    setSaveError(null);
    if (orgMissing) {
      setSaveError('Enter the organization, group, or workspace the tool should search and push upgrade branches to.');
      return;
    }
    try {
      setSaving(true);
      await saveSettings({
        platform,
        host: host.trim() || undefined,
        org: org.trim() || undefined,
        secretRef: secretRef.trim() || undefined,
        detectionMethod,
        repoTag: repoTag.trim() || DEFAULT_REPO_TAG,
        // Omit the branch tag when empty so it is not persisted.
        branchTag: branchTag.trim() || undefined,
      });
      setSaved(true);
    } catch (err: any) {
      setSaveError(err?.message || "The settings couldn't be saved. Try again later.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Configure how the tool reaches your Git platform and how it finds each function's source repository. Today a migration reads the repository tag, the branch tag and the organization; platform, host, secret reference and detection method are saved for later use. Nothing here starts a migration."
        >
          Settings
        </Header>
      }
    >
      {loading ? (
        <Container>
          <StatusIndicator type="loading">Loading settings</StatusIndicator>
        </Container>
      ) : loadError ? (
        <Container>
          <SpaceBetween size="s">
            <StatusIndicator type="error">{loadError}</StatusIndicator>
            <Button iconName="refresh" onClick={load}>Retry</Button>
          </SpaceBetween>
        </Container>
      ) : (
        <Form
          actions={
            <SpaceBetween direction="horizontal" size="xs" alignItems="center">
              {saved && <StatusIndicator type="success">Settings saved</StatusIndicator>}
              <Button variant="primary" onClick={handleSave} loading={saving}>
                Save settings
              </Button>
            </SpaceBetween>
          }
        >
          <SpaceBetween size="l">
            {saveError && (
              <Alert type="error" header="Couldn't save settings" dismissible onDismiss={() => setSaveError(null)}>
                {saveError}
              </Alert>
            )}

            {/* Section 1 — Git platform access */}
            <Container
              header={
                <Header
                  variant="h2"
                  description="The tool pushes an upgrade branch to the repository that holds each Lambda function's source code, so it needs read and push access to your Git platform."
                >
                  Git platform access
                </Header>
              }
            >
              <SpaceBetween size="l">
                <FormField
                  label="Platform"
                  description="The Git platform that hosts your source code. One platform per deployment."
                >
                  <Select
                    selectedOption={PLATFORM_OPTIONS.find((o) => o.value === platform) || PLATFORM_OPTIONS[0]}
                    onChange={({ detail }) => setPlatform((detail.selectedOption.value as GitPlatform) || 'github')}
                    options={PLATFORM_OPTIONS}
                  />
                </FormField>

                <FormField
                  label={
                    <span>
                      Server / host URL <i>- optional</i>
                    </span>
                  }
                  description="Self-hosted or enterprise only; leave blank for the SaaS default. Not used for CodeCommit, which is implicit from your AWS account."
                >
                  <Input
                    value={host}
                    onChange={({ detail }) => setHost(detail.value)}
                    placeholder="https://github.com or https://gitlab.example.com"
                    disabled={isCodeCommit}
                  />
                </FormField>

                <FormField
                  label={orgRequired ? 'Organization / group / workspace' : (
                    <span>
                      Organization / group / workspace <i>- optional</i>
                    </span>
                  )}
                  description="The scope the tool searches and pushes upgrade branches to. Required for GitHub, GitLab, and Bitbucket; CodeCommit scopes to your AWS account."
                  errorText={orgMissing ? 'Required for the selected platform.' : undefined}
                >
                  <Input
                    value={org}
                    onChange={({ detail }) => setOrg(detail.value)}
                    placeholder={isCodeCommit ? 'Not required for CodeCommit' : 'my-org (e.g. acme, example-corp)'}
                    disabled={isCodeCommit}
                  />
                </FormField>

                <FormField
                  label={
                    <span>
                      Credential <i>- optional</i>
                    </span>
                  }
                  description="Store your Git token in AWS Secrets Manager and reference it here; the tool reads it server-side. CodeCommit needs no token — it uses the task's IAM role."
                >
                  <Input
                    value={secretRef}
                    onChange={({ detail }) => setSecretRef(detail.value)}
                    placeholder="<SECRETS_MANAGER_SECRET_NAME>"
                    disabled={isCodeCommit}
                  />
                </FormField>

                <Alert type="info" header="Least-privilege access">
                  The token needs only repository read and push scope — nothing more.
                  Never grant it production write access.
                </Alert>
              </SpaceBetween>
            </Container>

            {/* Section 2 — code-behind mapping detection */}
            <Container
              header={
                <Header
                  variant="h2"
                  description="A deployed Lambda has no native attribute pointing to its source repository — AWS does not record it — so the tool must detect which repository holds each function's code before it can push a migration branch to it."
                >
                  AWS Lambda function code-behind mapping detection
                </Header>
              }
            >
              <SpaceBetween size="l">
                <Tiles
                  value={detectionMethod}
                  onChange={({ detail }) => setDetectionMethod(detail.value as DetectionMethod)}
                  items={[
                    {
                      value: 'tag',
                      label: 'Tag-based mapping',
                      description:
                        'The tool reads a resource tag on each Lambda whose value names the source repository. Fast and exact when your functions carry such a tag.',
                    },
                    {
                      value: 'ai',
                      label: 'AI-driven detection — Playground',
                      description:
                        'No repository tag? An AI resolver infers the repository from the function\'s tags, name, and a search of your Git organization — returning a confidence score with evidence. Try it on sample functions.',
                    },
                  ]}
                />

                {detectionMethod === 'tag' && (
                  <SpaceBetween size="m">
                    <FormField
                      label="Tag that holds the repository"
                      description="Pick the resource tag whose value is the source repository. The organization (set above) is prepended, so the tag can be a short name."
                    >
                      <Input
                        value={repoTag}
                        onChange={({ detail }) => setRepoTag(detail.value)}
                        placeholder="Git:Repository"
                      />
                    </FormField>

                    <FormField
                      label={
                        <span>
                          Tag that holds the branch <i>- optional</i>
                        </span>
                      }
                      description="Pick the tag whose value is the release branch, if a separate tag carries it. Leave blank to use the repository's default branch."
                    >
                      <Input
                        value={branchTag}
                        onChange={({ detail }) => setBranchTag(detail.value)}
                        placeholder="git:branch"
                      />
                    </FormField>

                    {/* Illustrative resolution from the current field values — no
                        backend call; the <value of …> token shows which tag supplies
                        the repository without inventing a repository name. */}
                    <SpaceBetween size="xxs">
                      <Box variant="awsui-key-label">Resolves to, per function</Box>
                      <Box>
                        <code>
                          {repoTag.trim()
                            ? `${org.trim() || 'my-org'}/<value of ${repoTag.trim()}>`
                            : '—'}
                        </code>
                        {repoTag.trim() && (
                          branchTag.trim()
                            ? ` @ <value of ${branchTag.trim()}>`
                            : ' (on the default branch)'
                        )}
                      </Box>
                    </SpaceBetween>

                    <Alert type="info">
                      Most fleets need only the first field. Set the branch tag only when the
                      repository and branch live in two different tags. A function missing the
                      chosen tag resolves to no repository — it is reported, never guessed.
                    </Alert>
                  </SpaceBetween>
                )}

                {detectionMethod === 'ai' && (
                  <SpaceBetween size="m">
                    <Box color="text-body-secondary" fontSize="body-s">
                      Describe how your repositories are organized, then submit. The playground picks 3
                      functions at random from your inventory and shows what the AI resolver would find —
                      the source repository and a confidence score with its evidence. Nothing is changed;
                      nothing is pushed.
                    </Box>

                    <PromptInput
                      value={aiPrompt}
                      onChange={({ detail }) => setAiPrompt(detail.value)}
                      onAction={runPlayground}
                      actionButtonIconName="send"
                      disabled={aiLoading}
                      placeholder="Our repos are named after the service, hosted in the acme GitLab group. The cmdb-id tag maps to a project via our internal registry."
                      i18nStrings={{ actionButtonAriaLabel: 'Submit prompt' }}
                    />

                    <SpaceBetween direction="horizontal" size="xs">
                      {SAMPLE_PROMPTS.map((text) => (
                        <Button
                          key={text}
                          variant="inline-link"
                          disabled={aiLoading}
                          onClick={() => setAiPrompt(text)}
                        >
                          {text}
                        </Button>
                      ))}
                    </SpaceBetween>

                    {aiLoading && (
                      <SpaceBetween size="xs">
                        <StatusIndicator type="loading">Resolving source repositories</StatusIndicator>
                        <SpaceBetween size="xxs">
                          {STEP_LABELS.map((label, i) => {
                            if (i < stepIndex) {
                              return (
                                <StatusIndicator key={label} type="success">{label}</StatusIndicator>
                              );
                            }
                            if (i === stepIndex) {
                              return (
                                <StatusIndicator key={label} type="loading">{label}</StatusIndicator>
                              );
                            }
                            return (
                              <Box key={label} color="text-body-secondary" fontSize="body-s">
                                {label}
                              </Box>
                            );
                          })}
                        </SpaceBetween>
                      </SpaceBetween>
                    )}

                    {aiError && (
                      <Alert type="error" header="Couldn't resolve repositories" dismissible onDismiss={() => setAiError(null)}>
                        <SpaceBetween size="s">
                          <span>{aiError}</span>
                          <Button iconName="refresh" disabled={aiLoading} onClick={runPlayground}>Retry</Button>
                        </SpaceBetween>
                      </Alert>
                    )}

                    {aiResults && !aiLoading && (
                      <SpaceBetween size="m">
                        <Box variant="awsui-gen-ai-label">
                          <Icon name="gen-ai" /> Generated by AI
                        </Box>

                        {aiResults.map((r) => {
                          const tier = confidenceTier(r.confidence);
                          const pct = Math.round(r.confidence * 100);
                          const meta = [r.runtime, r.region, r.account_id ? `account ${r.account_id}` : null]
                            .filter(Boolean)
                            .join(' · ');
                          return (
                            <Container
                              key={r.function_arn}
                              header={
                                <Header
                                  variant="h3"
                                  description={meta || undefined}
                                  actions={
                                    <StatusIndicator type={tier.indicator}>
                                      {pct}% · {tier.label}
                                    </StatusIndicator>
                                  }
                                >
                                  {r.function_name}
                                </Header>
                              }
                            >
                              <SpaceBetween size="s">
                                {r.repository ? (
                                  <Box>Source repository · <code>{r.repository}</code></Box>
                                ) : (
                                  <Box color="text-body-secondary">
                                    <i>No repository resolved — sent to review, not guessed.</i>
                                  </Box>
                                )}
                                <ul>
                                  {r.evidence.map((e, i) => (
                                    <li key={i}>{e}</li>
                                  ))}
                                </ul>
                              </SpaceBetween>
                            </Container>
                          );
                        })}

                        <Box color="text-body-secondary" fontSize="body-s">
                          These are the resolver's best inferences, not confirmed facts. In the migration
                          phase the pushed branch is the hand-off — your team opens the pull request and
                          reviews it before anything merges.
                          Low-confidence matches are sent to review; unresolvable functions are reported,
                          never guessed.
                        </Box>
                      </SpaceBetween>
                    )}
                  </SpaceBetween>
                )}
              </SpaceBetween>
            </Container>

            <Box color="text-body-secondary" fontSize="body-s">
              Saved repository tag, branch tag and organization are read by every migration. Platform, host,
              secret reference and detection method are stored but not used yet. Nothing on this page starts a migration.
            </Box>
          </SpaceBetween>
        </Form>
      )}
    </ContentLayout>
  );
}
