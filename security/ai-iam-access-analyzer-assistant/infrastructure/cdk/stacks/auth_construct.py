"""Cognito authentication construct."""

from aws_cdk import (
    RemovalPolicy,
    aws_cognito as cognito,
    CfnResource,
)
from constructs import Construct


class AuthConstruct(Construct):
    """Cognito User Pool + Identity Pool for frontend authentication."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # User Pool
        self.user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name="iam-analyzer-assistant-users",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=False,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # App Client (for frontend)
        self.user_pool_client = self.user_pool.add_client(
            "WebAppClient",
            user_pool_client_name="iam-analyzer-web-client",
            auth_flows=cognito.AuthFlow(
                user_srp=True,
                user_password=True,
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(implicit_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
            ),
        )

        # Identity Pool (for temporary AWS credentials if needed)
        self.identity_pool = CfnResource(
            self,
            "IdentityPool",
            type="AWS::Cognito::IdentityPool",
            properties={
                "IdentityPoolName": "iam-analyzer-assistant-identity",
                "AllowUnauthenticatedIdentities": False,
                "CognitoIdentityProviders": [
                    {
                        "ClientId": self.user_pool_client.user_pool_client_id,
                        "ProviderName": self.user_pool.user_pool_provider_name,
                    }
                ],
            },
        )
