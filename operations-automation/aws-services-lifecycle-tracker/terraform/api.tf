# =============================================================================
# api.tf - HTTP API in front of the API function (CDK: api-stack.ts)
# =============================================================================
# The Cognito user-pool JWT authorizer validates the ID token the SPA sends in
# the Authorization header; the browser itself holds no AWS credentials.
#
#   POST /actions          router actions (list_services, update_service, ...)
#   POST /refresh          start (or adopt) a durable pipeline execution
#   GET  /refresh/{arn+}   execution status for UI polling / re-attach
# =============================================================================

resource "aws_apigatewayv2_api" "http" {
  name          = "aws-services-lifecycle-tracker-api"
  description   = "AWS Services Lifecycle Tracker UI API (Cognito JWT)"
  protocol_type = "HTTP"

  cors_configuration {
    # The CloudFront domain is only known after the frontend deploys, which
    # itself needs this API URL at build time; auth is enforced by the JWT
    # authorizer, not by the origin allow-list.
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["Content-Type", "Authorization"]
    max_age       = 3600
  }

  tags = local.tags
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.http.id
  name             = "CognitoAuthorizer"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    issuer   = "https://${aws_cognito_user_pool.admin.endpoint}"
    audience = [aws_cognito_user_pool_client.web.id]
  }
}

resource "aws_apigatewayv2_integration" "api_function" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

locals {
  api_routes = {
    actions        = "POST /actions"
    refresh_start  = "POST /refresh"
    refresh_status = "GET /refresh/{arn+}" # greedy: durable execution ARNs contain '/'
  }
}

resource "aws_apigatewayv2_route" "routes" {
  for_each = local.api_routes

  api_id             = aws_apigatewayv2_api.http.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.api_function.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
  tags        = local.tags
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowHttpApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
