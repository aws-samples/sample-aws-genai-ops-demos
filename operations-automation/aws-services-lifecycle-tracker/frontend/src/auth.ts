/**
 * AWS Cognito Authentication Module
 * 
 * TOKEN TYPES EXPLAINED:
 * 
 * 🆔 ID Token (getIdToken):
 *    - WHO the user is (identity)
 *    - Contains: sub, email, name, aud (audience)
 *    - Used for: Cognito Identity Pool → AWS credentials
 *    - Required for: Our AgentCore access via IAM
 * 
 * 🔑 Access Token (getAccessToken):
 *    - WHAT the user can access (authorization)
 *    - Contains: scopes, permissions, client_id
 *    - Used for: Direct API calls with OAuth2 scopes
 *    - NOT used for: Cognito Identity Pool (missing 'aud' claim)
 * 
 * 🔄 Refresh Token:
 *    - Used to get new ID/Access tokens when they expire
 *    - Automatically handled in getAccessToken()
 * 
 * OUR ARCHITECTURE:
 * User → Cognito User Pool → ID Token → Cognito Identity Pool → AWS Credentials → AgentCore (IAM)
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
  throw new Error('Missing Cognito configuration');
}

const userPool = new CognitoUserPool({
  UserPoolId: userPoolId,
  ClientId: clientId,
});

export interface AuthUser {
  username: string;
  email: string;
}

export const signUp = (email: string, password: string): Promise<void> => {
  return new Promise((resolve, reject) => {
    const attributeList = [
      new CognitoUserAttribute({
        Name: 'email',
        Value: email,
      }),
    ];

    userPool.signUp(email, password, attributeList, [], (err) => {
      if (err) {
        reject(err);
        return;
      }
      resolve();
    });
  });
};

export const confirmSignUp = (email: string, code: string): Promise<void> => {
  return new Promise((resolve, reject) => {
    const cognitoUser = new CognitoUser({
      Username: email,
      Pool: userPool,
    });

    cognitoUser.confirmRegistration(code, true, (err) => {
      if (err) {
        reject(err);
        return;
      }
      resolve();
    });
  });
};

export const signIn = (email: string, password: string): Promise<string> => {
  return new Promise((resolve, reject) => {
    const authenticationDetails = new AuthenticationDetails({
      Username: email,
      Password: password,
    });

    const cognitoUser = new CognitoUser({
      Username: email,
      Pool: userPool,
    });

    cognitoUser.authenticateUser(authenticationDetails, {
      onSuccess: (result) => {
        const idToken = result.getIdToken().getJwtToken();
        resolve(idToken);
      },
      onFailure: (err) => {
        reject(err);
      },
    });
  });
};

export const signOut = (): void => {
  const cognitoUser = userPool.getCurrentUser();
  if (cognitoUser) {
    cognitoUser.signOut();
  }
};

export const getCurrentUser = (): Promise<AuthUser | null> => {
  return new Promise((resolve) => {
    const cognitoUser = userPool.getCurrentUser();

    if (!cognitoUser) {
      resolve(null);
      return;
    }

    cognitoUser.getSession((err: any, session: any) => {
      if (err || !session.isValid()) {
        resolve(null);
        return;
      }

      cognitoUser.getUserAttributes((err, attributes) => {
        if (err) {
          resolve(null);
          return;
        }

        const email = attributes?.find((attr) => attr.Name === 'email')?.Value || '';

        resolve({
          username: cognitoUser.getUsername(),
          email,
        });
      });
    });
  });
};

/**
 * Gets the Cognito ID Token - used for IDENTITY verification
 * 
 * ID Token contains:
 * - User identity information (sub, email, name, etc.)
 * - 'aud' claim (audience) - required by Cognito Identity Pool
 * - Used to prove "who the user is"
 * 
 * Use cases:
 * - Cognito Identity Pool authentication (to get AWS credentials)
 * - User profile information
 * - Single Sign-On (SSO)
 */
export const getIdToken = (): Promise<string | null> => {
  return new Promise((resolve) => {
    const cognitoUser = userPool.getCurrentUser();

    if (!cognitoUser) {
      resolve(null);
      return;
    }

    cognitoUser.getSession((err: any, session: any) => {
      if (err || !session.isValid()) {
        resolve(null);
        return;
      }

      // ID Token - contains user identity claims including 'aud' (audience)
      resolve(session.getIdToken().getJwtToken());
    });
  });
};

/**
 * Gets the Cognito Access Token - used for AUTHORIZATION to access resources
 * 
 * Access Token contains:
 * - Scopes and permissions (what the user can do)
 * - Client ID and token use information
 * - Used to prove "what the user can access"
 * 
 * Use cases:
 * - Direct API calls to services that accept Cognito Access Tokens
 * - Resource server authorization (when using OAuth2 scopes)
 * - Third-party API access (when configured with OAuth2 flows)
 * 
 * Note: Does NOT contain 'aud' claim - cannot be used with Cognito Identity Pool
 */
export const getAccessToken = (): Promise<string | null> => {
  return new Promise((resolve, reject) => {
    const cognitoUser = userPool.getCurrentUser();

    if (!cognitoUser) {
      resolve(null);
      return;
    }

    cognitoUser.getSession((err: any, session: any) => {
      if (err) {
        resolve(null);
        return;
      }

      if (!session.isValid()) {
        resolve(null);
        return;
      }

      // Check if token is about to expire (within 5 minutes)
      const expiresAt = session.getAccessToken().getExpiration() * 1000; // Convert to milliseconds
      const now = Date.now();
      const fiveMinutes = 5 * 60 * 1000;

      if (expiresAt - now < fiveMinutes) {
        // Token is about to expire, refresh it
        const refreshToken = session.getRefreshToken();
        cognitoUser.refreshSession(refreshToken, (refreshErr: any, newSession: any) => {
          if (refreshErr) {
            console.error('Token refresh failed:', refreshErr);
            resolve(null);
            return;
          }
          // Access Token - contains authorization scopes, NOT identity claims
          resolve(newSession.getAccessToken().getJwtToken());
        });
      } else {
        // Token is still valid - Access Token for authorization
        resolve(session.getAccessToken().getJwtToken());
      }
    });
  });
};
