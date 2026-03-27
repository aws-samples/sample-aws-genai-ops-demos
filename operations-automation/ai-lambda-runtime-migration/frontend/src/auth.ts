/**
 * AWS Cognito Authentication Module
 * 
 * Uses amazon-cognito-identity-js for User Pool authentication.
 * ID Token is used for API Gateway Cognito authorizer.
 */

import {
  CognitoUserPool,
  CognitoUser,
  AuthenticationDetails,
  CognitoUserAttribute,
} from 'amazon-cognito-identity-js';

const userPoolId = import.meta.env.VITE_USER_POOL_ID;
const clientId = import.meta.env.VITE_USER_POOL_CLIENT_ID;

if (!userPoolId || !clientId) {
  console.warn('Missing Cognito configuration — auth will not work');
}

const userPool = new CognitoUserPool({
  UserPoolId: userPoolId || '',
  ClientId: clientId || '',
});

export interface AuthUser {
  username: string;
  email: string;
}

export const signUp = (email: string, password: string): Promise<void> => {
  return new Promise((resolve, reject) => {
    const attributeList = [
      new CognitoUserAttribute({ Name: 'email', Value: email }),
    ];
    userPool.signUp(email, password, attributeList, [], (err) => {
      if (err) { reject(err); return; }
      resolve();
    });
  });
};

export const confirmSignUp = (email: string, code: string): Promise<void> => {
  return new Promise((resolve, reject) => {
    const cognitoUser = new CognitoUser({ Username: email, Pool: userPool });
    cognitoUser.confirmRegistration(code, true, (err) => {
      if (err) { reject(err); return; }
      resolve();
    });
  });
};

export const signIn = (email: string, password: string): Promise<string> => {
  return new Promise((resolve, reject) => {
    const authDetails = new AuthenticationDetails({ Username: email, Password: password });
    const cognitoUser = new CognitoUser({ Username: email, Pool: userPool });
    cognitoUser.authenticateUser(authDetails, {
      onSuccess: (result) => resolve(result.getIdToken().getJwtToken()),
      onFailure: (err) => reject(err),
    });
  });
};

export const signOut = (): void => {
  const cognitoUser = userPool.getCurrentUser();
  if (cognitoUser) cognitoUser.signOut();
};

export const getCurrentUser = (): Promise<AuthUser | null> => {
  return new Promise((resolve) => {
    const cognitoUser = userPool.getCurrentUser();
    if (!cognitoUser) { resolve(null); return; }
    cognitoUser.getSession((err: any, session: any) => {
      if (err || !session.isValid()) { resolve(null); return; }
      cognitoUser.getUserAttributes((err, attributes) => {
        if (err) { resolve(null); return; }
        const email = attributes?.find((a) => a.Name === 'email')?.Value || '';
        resolve({ username: cognitoUser.getUsername(), email });
      });
    });
  });
};

export const getIdToken = (): Promise<string | null> => {
  return new Promise((resolve) => {
    const cognitoUser = userPool.getCurrentUser();
    if (!cognitoUser) { resolve(null); return; }
    cognitoUser.getSession((err: any, session: any) => {
      if (err || !session.isValid()) { resolve(null); return; }
      resolve(session.getIdToken().getJwtToken());
    });
  });
};
