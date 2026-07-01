import { useState } from 'react';
import { Button, FormField, Input, Modal, SpaceBetween, Alert } from '@cloudscape-design/components';
import { signIn } from './auth';

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
    <Modal visible header="Sign in to AI-Assisted Security Triage" closeAriaLabel="Close">
      <form
        onSubmit={(e) => { e.preventDefault(); submit(); }}
        autoComplete="on"
      >
        <SpaceBetween size="m">
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
            onClick={() => submit()}
            loading={loading}
            disabled={!email || !password || loading}
            loadingText="Signing in…"
          >
            Sign in
          </Button>
        </SpaceBetween>
      </form>
    </Modal>
  );
}
