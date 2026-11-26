import { useState } from 'react';
import { Alert, Box, Button, FormField, Input, SpaceBetween } from '@cloudscape-design/components';
import { signIn } from './auth';
import { BrandMark } from './ui';

export default function AuthModal({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!email || !password) return;
    setLoading(true);
    setError(null);
    try {
      await signIn(email, password);
      onAuthenticated();
    } catch (err: any) {
      setError(err?.message || 'Sign-in failed');
    } finally {
      setLoading(false);
    }
  }

  // Wrapping the controls in a <form> lets the browser fire submit on Enter in
  // either field without us having to bind onKeyDown per input.
  return (
    <div className="soc-auth">
      <aside className="soc-auth-story" aria-label="About this demo">
        <span className="soc-auth-brand"><BrandMark size={28} />AI-Assisted Security Triage</span>
        <div>
          <p className="soc-auth-claim">Every Prowler finding, triaged and explained.</p>
          <ul className="soc-auth-steps">
            <li><strong>Prowler</strong><span>Scans this AWS account on Fargate and records every check.</span></li>
            <li><strong>Bedrock</strong><span>Writes a remediation playbook for the finding you open.</span></li>
            <li><strong>DevOps Agent</strong><span>Investigates the resource and streams its reasoning back.</span></li>
          </ul>
        </div>
        <div>
          <div className="soc-auth-ramp" aria-hidden="true"><span /><span /><span /><span /><span /></div>
          <p className="soc-auth-foot">Critical to informational, the order you fix them in.</p>
        </div>
      </aside>

      <main className="soc-auth-panel">
        <div className="soc-auth-card">
          <span className="soc-auth-mobile-brand"><BrandMark size={28} />AI-Assisted Security Triage</span>
          <h1>Sign in</h1>
          <p className="soc-auth-sub">Use the dashboard user created by the deploy script.</p>
          <form
            onSubmit={(e) => { e.preventDefault(); submit(); }}
            autoComplete="on"
            aria-label="Sign in to AI-Assisted Security Triage"
          >
            <SpaceBetween size="l">
              {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
              <FormField label="Email">
                <Input
                  value={email}
                  onChange={(e) => setEmail(e.detail.value)}
                  type="email"
                  name="email"
                  autoComplete="email"
                  inputMode="email"
                  spellcheck={false}
                  disableBrowserAutocorrect
                  autoFocus
                  placeholder="you@example.com"
                  ariaRequired
                />
              </FormField>
              <FormField label="Password">
                <Input
                  value={password}
                  onChange={(e) => setPassword(e.detail.value)}
                  type="password"
                  name="password"
                  autoComplete="current-password"
                  spellcheck={false}
                  ariaRequired
                />
              </FormField>
              <Button
                variant="primary"
                formAction="submit"
                fullWidth
                loading={loading}
                disabled={!email || !password || loading}
                loadingText="Signing in…"
              >
                Sign in
              </Button>
            </SpaceBetween>
          </form>
          <Box variant="p" color="text-status-inactive" fontSize="body-s" margin={{ top: 'l' }}>
            Authentication runs through Amazon Cognito. Your session stays in this browser until you sign out.
          </Box>
        </div>
      </main>
    </div>
  );
}
