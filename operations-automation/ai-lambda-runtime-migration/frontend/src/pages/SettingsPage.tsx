// Settings — the tag keys the migration reads to find each Lambda function's
// source repository in AWS CodeCommit before it can push an upgrade branch.
//
// Nothing on this page triggers a migration: it only CONFIGURES detection.
//
// The two fields are exactly what the backend reads (migrate.resolve_and_verify
// via inventory.read_settings): a required repository tag and an optional
// branch tag. They are persisted via the existing /actions dispatcher
// (get_settings / put_settings) into the existing table under a settings# key.
// Modeled on StatisticsPage: ContentLayout + Header, load on mount, Cloudscape
// loading / error (+Retry) conventions.
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Form from '@cloudscape-design/components/form';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Button from '@cloudscape-design/components/button';
import Box from '@cloudscape-design/components/box';
import Link from '@cloudscape-design/components/link';
import Alert from '@cloudscape-design/components/alert';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { getSettings, saveSettings } from '../api';
import { DEFAULT_REPO_TAG } from '../constants';

export default function SettingsPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Tag that names the repository; the default applies until a value is saved.
  const [repoTag, setRepoTag] = useState(DEFAULT_REPO_TAG);
  // Optional tag that names the source branch; empty = default branch.
  const [branchTag, setBranchTag] = useState('');

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setLoadError(null);
      const { settings } = await getSettings();
      // An unset tag shows the default the backend applies.
      setRepoTag(settings.repoTag || DEFAULT_REPO_TAG);
      setBranchTag(settings.branchTag || '');
    } catch (err) {
      setLoadError((err instanceof Error && err.message) || "The settings couldn't be fetched. Try again later.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleSave = async () => {
    setSaved(false);
    setSaveError(null);
    try {
      setSaving(true);
      await saveSettings({
        repoTag: repoTag.trim() || DEFAULT_REPO_TAG,
        // Omit the branch tag when empty so it is not persisted.
        branchTag: branchTag.trim() || undefined,
      });
      setSaved(true);
    } catch (err) {
      setSaveError((err instanceof Error && err.message) || "The settings couldn't be saved. Try again later.");
    } finally {
      setSaving(false);
    }
  };

  const repoTagShown = repoTag.trim() || DEFAULT_REPO_TAG;

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description={
            <>
              This demo resolves each function's source repository in AWS CodeCommit, in the function's
              account and region, from the function's tags. Other Git hosts would differ only in build
              authentication and the branch link; see{' '}
              <Link href="/how-it-works" onFollow={(e) => { e.preventDefault(); navigate('/how-it-works'); }}>How it works</Link>.
              Nothing here starts a migration.
            </>
          }
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

            <Container
              header={
                <Header
                  variant="h2"
                  description="A deployed Lambda has no native attribute pointing to its source repository — AWS does not record it — so the migration reads a resource tag on each function. These two tag keys are the only configuration a migration reads."
                >
                  Source repository tags
                </Header>
              }
            >
              <SpaceBetween size="l">
                <FormField
                  label="Tag that holds the repository"
                  description={`The resource tag whose value is the CodeCommit repository name. Blank saves the default (${DEFAULT_REPO_TAG}).`}
                >
                  <Input
                    value={repoTag}
                    onChange={({ detail }) => setRepoTag(detail.value)}
                    placeholder={DEFAULT_REPO_TAG}
                  />
                </FormField>

                <FormField
                  label={
                    <span>
                      Tag that holds the branch <i>- optional</i>
                    </span>
                  }
                  description="The resource tag whose value is the branch to clone and upgrade from, if a separate tag carries it. Leave blank to use the repository's default branch."
                >
                  <Input
                    value={branchTag}
                    onChange={({ detail }) => setBranchTag(detail.value)}
                    placeholder="git-branch"
                  />
                </FormField>

                {/* Illustrative resolution from the current field values — no
                    backend call; the <value of …> token shows which tag supplies
                    the repository without inventing a repository name. */}
                <SpaceBetween size="xxs">
                  <Box variant="awsui-key-label">Resolves to, per function</Box>
                  <Box>
                    <code>{`<value of ${repoTagShown}>`}</code>
                    {branchTag.trim()
                      ? ` @ <value of ${branchTag.trim()}>`
                      : ' (on the default branch)'}
                  </Box>
                </SpaceBetween>

                <Alert type="info">
                  Most fleets need only the first field. Set the branch tag only when the
                  repository and branch live in two different tags. A function missing the
                  chosen tag resolves to no repository — it is reported, never guessed.
                </Alert>
              </SpaceBetween>
            </Container>
          </SpaceBetween>
        </Form>
      )}
    </ContentLayout>
  );
}
