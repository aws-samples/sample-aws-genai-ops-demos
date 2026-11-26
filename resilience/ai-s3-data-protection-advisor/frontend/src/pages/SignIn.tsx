import { useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Form from '@cloudscape-design/components/form';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import SpaceBetween from '@cloudscape-design/components/space-between';
import { signIn } from '../auth';

interface Props {
  onSignedIn: () => void;
}

export default function SignIn({ onSignedIn }: Props) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [complete, setComplete] = useState<((p: string) => Promise<void>) | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submitSignIn = async () => {
    setBusy(true);
    setError('');
    try {
      const result = await signIn(email.trim(), password);
      if (result.kind === 'signedIn') onSignedIn();
      else setComplete(() => result.complete);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submitNewPassword = async () => {
    if (newPassword !== confirm) {
      setError('The two passwords do not match.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await complete!(newPassword);
      onSignedIn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const settingPassword = complete !== null;

  return (
    <Box margin={{ top: 'xxxl' }}>
      <div style={{ maxWidth: 480, margin: '0 auto' }}>
        <Container header={<Header variant="h1">{settingPassword ? 'Set a new password' : 'Sign in'}</Header>}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!busy) void (settingPassword ? submitNewPassword() : submitSignIn());
            }}
          >
            <Form
              errorText={error || undefined}
              actions={
                <Button variant="primary" formAction="submit" loading={busy}>
                  {settingPassword ? 'Set password and sign in' : 'Sign in'}
                </Button>
              }
            >
              <SpaceBetween size="m">
                {settingPassword ? (
                  <>
                    <Alert type="info">Your temporary password must be replaced before you continue.</Alert>
                    <FormField label="New password">
                      <Input type="password" value={newPassword} onChange={({ detail }) => setNewPassword(detail.value)} />
                    </FormField>
                    <FormField label="Confirm new password">
                      <Input type="password" value={confirm} onChange={({ detail }) => setConfirm(detail.value)} />
                    </FormField>
                  </>
                ) : (
                  <>
                    <FormField label="Email">
                      <Input type="email" value={email} onChange={({ detail }) => setEmail(detail.value)} autoFocus />
                    </FormField>
                    <FormField label="Password">
                      <Input type="password" value={password} onChange={({ detail }) => setPassword(detail.value)} />
                    </FormField>
                  </>
                )}
              </SpaceBetween>
            </Form>
          </form>
        </Container>
      </div>
    </Box>
  );
}
