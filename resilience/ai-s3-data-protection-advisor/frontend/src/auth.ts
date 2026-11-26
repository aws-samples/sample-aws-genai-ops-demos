import {
  AuthenticationDetails,
  CognitoUser,
  CognitoUserPool,
  CognitoUserSession,
} from 'amazon-cognito-identity-js';
import { getConfig } from './config';

export type SignInResult =
  | { kind: 'signedIn' }
  | { kind: 'newPasswordRequired'; complete: (newPassword: string) => Promise<void> };

function pool(): CognitoUserPool {
  const cfg = getConfig();
  return new CognitoUserPool({ UserPoolId: cfg.userPoolId, ClientId: cfg.userPoolClientId });
}

/** Maps a Cognito error to plain text for the sign-in form. */
export function authMessage(err: unknown): string {
  const code = (err as { code?: string; name?: string })?.code ?? (err as { name?: string })?.name ?? '';
  switch (code) {
    case 'NotAuthorizedException':
    case 'UserNotFoundException':
      return 'Incorrect email or password.';
    case 'InvalidPasswordException':
      return 'The new password does not meet the password policy.';
    case 'PasswordResetRequiredException':
      return 'A password reset is required. Contact the administrator.';
    case 'UserNotConfirmedException':
      return 'This account is not confirmed. Contact the administrator.';
    case 'TooManyRequestsException':
    case 'LimitExceededException':
      return 'Too many attempts. Wait a moment and try again.';
    case 'NetworkError':
      return 'The sign-in service could not be reached.';
    default:
      return 'Sign-in failed. Try again.';
  }
}

export function signIn(email: string, password: string): Promise<SignInResult> {
  return new Promise((resolve, reject) => {
    const user = new CognitoUser({ Username: email, Pool: pool() });
    const details = new AuthenticationDetails({ Username: email, Password: password });
    user.authenticateUser(details, {
      onSuccess: () => resolve({ kind: 'signedIn' }),
      onFailure: (err) => reject(new Error(authMessage(err))),
      newPasswordRequired: () =>
        resolve({
          kind: 'newPasswordRequired',
          complete: (newPassword: string) =>
            new Promise<void>((res, rej) => {
              // The attributes Cognito already holds are not resubmitted.
              user.completeNewPasswordChallenge(newPassword, {}, {
                onSuccess: () => res(),
                onFailure: (err) => rej(new Error(authMessage(err))),
              });
            }),
        }),
    });
  });
}

export function signOut(): void {
  try {
    pool().getCurrentUser()?.signOut();
  } catch {
    // Not configured: nothing to sign out of.
  }
}

function session(): Promise<CognitoUserSession | null> {
  return new Promise((resolve) => {
    let user: CognitoUser | null = null;
    try {
      user = pool().getCurrentUser();
    } catch {
      resolve(null);
      return;
    }
    if (!user) {
      resolve(null);
      return;
    }
    user.getSession((err: Error | null, s: CognitoUserSession | null) => {
      resolve(err || !s || !s.isValid() ? null : s);
    });
  });
}

/** ID token, refreshed through getSession; null when signed out. */
export async function getIdToken(): Promise<string | null> {
  const s = await session();
  return s ? s.getIdToken().getJwtToken() : null;
}

/** Email claim of the current ID token; null when signed out. */
export async function currentEmail(): Promise<string | null> {
  const s = await session();
  if (!s) return null;
  const payload = s.getIdToken().payload as Record<string, unknown>;
  const email = payload.email ?? payload['cognito:username'];
  return typeof email === 'string' ? email : null;
}
