# =============================================================================
# auth.tf - Cognito user pool for the admin SPA (CDK: auth-stack.ts)
# =============================================================================
# Admin-only pool (no self-signup). The browser only needs the ID token: every
# AWS call goes through the HTTP API's JWT authorizer (issue #139), so there is
# no identity pool and no IAM role for authenticated users.
# =============================================================================

resource "aws_cognito_user_pool" "admin" {
  name = "aws-services-lifecycle-tracker-admin-users"

  # Sign in with username or email
  alias_attributes         = ["email"]
  auto_verified_attributes = ["email"]

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = false
  }

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  tags = local.tags
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "aws-services-lifecycle-tracker-web-client"
  user_pool_id = aws_cognito_user_pool.admin.id

  generate_secret               = false # public client (browser)
  prevent_user_existence_errors = "ENABLED"

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]
}
