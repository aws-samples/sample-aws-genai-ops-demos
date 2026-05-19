import {
  CognitoUserPool,
  CognitoUser,
  AuthenticationDetails,
} from 'amazon-cognito-identity-js';

const userPoolId = import.meta.env.VITE_USER_POOL_ID;
const clientId = import.meta.env.VITE_USER_POOL_CLIENT_ID;

const userPool = new CognitoUserPool({
  UserPoolId: userPoolId || '',
  ClientId: clientId || '',
});

export interface AuthUser { username: string; email: string; }

export const signIn = (email: string, password: string): Promise<string> =>
  new Promise((resolve, reject) => {
    const authDetails = new AuthenticationDetails({ Username: email, Password: password });
    const cognitoUser = new CognitoUser({ Username: email, Pool: userPool });
    cognitoUser.authenticateUser(authDetails, {
      onSuccess: (result) => resolve(result.getIdToken().getJwtToken()),
      onFailure: (err) => reject(err),
      newPasswordRequired: () => reject(new Error('New password required — set it via the AWS console, then try again.')),
    });
  });

export const signOut = (): void => {
  userPool.getCurrentUser()?.signOut();
};

export const getCurrentUser = (): Promise<AuthUser | null> =>
  new Promise((resolve) => {
    const cognitoUser = userPool.getCurrentUser();
    if (!cognitoUser) { resolve(null); return; }
    cognitoUser.getSession((err: any, session: any) => {
      if (err || !session.isValid()) { resolve(null); return; }
      cognitoUser.getUserAttributes((err2, attrs) => {
        if (err2) { resolve(null); return; }
        const email = attrs?.find((a) => a.Name === 'email')?.Value || '';
        resolve({ username: cognitoUser.getUsername(), email });
      });
    });
  });

export const getIdToken = (): Promise<string | null> =>
  new Promise((resolve) => {
    const cognitoUser = userPool.getCurrentUser();
    if (!cognitoUser) { resolve(null); return; }
    cognitoUser.getSession((err: any, session: any) => {
      if (err || !session.isValid()) { resolve(null); return; }
      resolve(session.getIdToken().getJwtToken());
    });
  });
