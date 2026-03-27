import { useState } from 'react';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Button from '@cloudscape-design/components/button';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Alert from '@cloudscape-design/components/alert';
import { signIn } from './auth';

interface AuthModalProps {
  visible: boolean;
  onDismiss: () => void;
  onSuccess: () => void;
}

export default function AuthModal({ visible, onDismiss, onSuccess }: AuthModalProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSignIn = async () => {
    setLoading(true);
    setError('');
    try {
      await signIn(email, password);
      onSuccess();
      setEmail('');
      setPassword('');
      setError('');
    } catch (err: any) {
      setError(err.message || 'Failed to sign in');
    } finally {
      setLoading(false);
    }
  };

  const handleDismiss = () => {
    setEmail('');
    setPassword('');
    setError('');
    onDismiss();
  };

  return (
    <Modal
      visible={visible}
      onDismiss={handleDismiss}
      header="Sign In"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={handleDismiss}>Cancel</Button>
            <Button variant="primary" onClick={handleSignIn} loading={loading}>Sign In</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        {error && (
          <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>
        )}
        <FormField label="Email">
          <Input value={email} onChange={({ detail }) => setEmail(detail.value)} type="email" placeholder="your@email.com" />
        </FormField>
        <FormField label="Password">
          <Input value={password} onChange={({ detail }) => setPassword(detail.value)} type="password" placeholder="Enter password" />
        </FormField>
      </SpaceBetween>
    </Modal>
  );
}
