import { useState } from 'react';
import { Button, FormField, Input, Modal, SpaceBetween, Alert } from '@cloudscape-design/components';
import { signIn } from './auth';

export default function AuthModal({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
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

  return (
    <Modal visible header="Sign in to Prowler Security Dashboard" closeAriaLabel="Close">
      <SpaceBetween size="m">
        {error && <Alert type="error">{error}</Alert>}
        <FormField label="Email">
          <Input value={email} onChange={(e) => setEmail(e.detail.value)} type="email" />
        </FormField>
        <FormField label="Password">
          <Input value={password} onChange={(e) => setPassword(e.detail.value)} type="password" />
        </FormField>
        <Button variant="primary" onClick={submit} loading={loading} disabled={!email || !password}>
          Sign in
        </Button>
      </SpaceBetween>
    </Modal>
  );
}
