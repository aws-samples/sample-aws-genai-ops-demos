"""API Gateway + Conversation Lambda construct."""

from pathlib import Path

from aws_cdk import (
    Duration,
    Fn,
    aws_apigateway as apigw,
    aws_cognito as cognito,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct


class ApiConstruct(Construct):
    """API Gateway with Cognito authorizer and conversation Lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        user_pool: cognito.IUserPool,
        tools_functions: dict,
        reports_bucket: s3.IBucket,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Path to backend source
        src_path = str(
            Path(__file__).resolve().parent.parent.parent.parent / "src"
        )

        # Conversation handler Lambda (orchestrates Bedrock + tools)
        self.conversation_fn = lambda_.Function(
            self,
            "ConversationHandler",
            runtime=lambda_.Runtime.PYTHON_3_14,
            handler="agent.handler",
            code=lambda_.Code.from_asset(src_path),
            timeout=Duration.seconds(120),
            memory_size=1024,
            environment={
                "REPORTS_BUCKET": reports_bucket.bucket_name,
                "BEDROCK_MODEL_ID": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "LIST_FINDINGS_FN": tools_functions["list_findings"].function_name,
                "GET_FINDING_DETAILS_FN": tools_functions["get_finding_details"].function_name,
                "GENERATE_POLICY_FN": tools_functions["generate_policy"].function_name,
                "CHECK_DEPENDENCIES_FN": tools_functions["check_dependencies"].function_name,
                "VALIDATE_POLICY_FN": tools_functions["validate_policy"].function_name,
                "EXPORT_REPORT_FN": tools_functions["export_report"].function_name,
                "GENERATE_ACTION_PLAN_FN": tools_functions["generate_action_plan"].function_name,
                "COMPARE_ROLES_FN": tools_functions["compare_roles"].function_name,
                "LIST_EXPORTS_FN": tools_functions["list_exports"].function_name,
                "TRIAGE_ACCESS_KEYS_FN": tools_functions["triage_access_keys"].function_name,
            },
        )

        # Bedrock permissions for conversation Lambda
        self.conversation_fn.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
                    "arn:aws:bedrock:*::foundation-model/us.anthropic.claude-*",
                    "arn:aws:bedrock:*:*:inference-profile/us.anthropic.claude-*",
                ],
            )
        )

        # Allow conversation Lambda to invoke tool Lambdas
        for fn in tools_functions.values():
            fn.grant_invoke(self.conversation_fn)

        # Session-start capability probe (#171 phase C). Runs the same
        # read-only checks deploy-all.sh runs at deploy time (#170) but from
        # inside the Lambda, so the frontend can render a "Data sources"
        # status line and an honest welcome message the moment the chat
        # opens. The endpoint is CHEAP: 3–5 AWS calls, sub-second in
        # aggregate, called ONCE per chat open.
        self.capabilities_fn = lambda_.Function(
            self,
            "CapabilitiesProbe",
            runtime=lambda_.Runtime.PYTHON_3_14,
            handler="capabilities.handler",
            code=lambda_.Code.from_asset(src_path),
            timeout=Duration.seconds(15),
            memory_size=256,
        )
        # Read-only permissions — mirrors the deploy-time probe's ACL.
        #
        # resources=["*"] is required by the AWS IAM authorization model for
        # every one of these five actions: they are account-scoped control-plane
        # calls with no resource-level authorization support. Per the AWS
        # service authorization reference:
        #
        #   * securityhub:DescribeHub, ListEnabledProductsForImport, GetFindings
        #     — supported resources column is "-" (none).
        #   * access-analyzer:ListAnalyzers — supported resources column is "-".
        #   * cloudtrail:LookupEvents — supported resources column is "-".
        #
        # A caller cannot write, for example, "arn:aws:securityhub:...:hub/xyz"
        # on DescribeHub — the policy would be rejected. The `*` here is the
        # least privilege AWS actually allows for these calls, and cfn-nag's
        # generic W11 warning does not apply to actions that inherently do not
        # accept resource ARNs. See:
        #   https://docs.aws.amazon.com/service-authorization/latest/reference/
        self.capabilities_fn.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "securityhub:DescribeHub",
                    "securityhub:ListEnabledProductsForImport",
                    "securityhub:GetFindings",
                    "access-analyzer:ListAnalyzers",
                    "cloudtrail:LookupEvents",
                ],
                resources=["*"],
            )
        )

        # API Gateway
        api = apigw.RestApi(
            self,
            "IamAnalyzerApi",
            rest_api_name="iam-analyzer-assistant-api",
            description="AI IAM Access Analyzer Assistant API",
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=apigw.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization"],
            ),
        )

        # Attach CORS headers to gateway-generated error responses (4xx/5xx).
        # Without these, an API Gateway integration timeout (504) or throttle
        # response is returned WITHOUT CORS headers, so the browser can't read it
        # and surfaces a generic "Failed to fetch" instead of the real status.
        # Adding them lets the frontend detect the timeout and show useful guidance.
        _error_cors_headers = {
            "Access-Control-Allow-Origin": "'*'",
            "Access-Control-Allow-Headers": "'Content-Type,Authorization'",
            "Access-Control-Allow-Methods": "'GET,POST,OPTIONS'",
        }
        api.add_gateway_response(
            "Default5xxCors",
            type=apigw.ResponseType.DEFAULT_5_XX,
            response_headers=_error_cors_headers,
        )
        api.add_gateway_response(
            "Default4xxCors",
            type=apigw.ResponseType.DEFAULT_4_XX,
            response_headers=_error_cors_headers,
        )

        # Cognito authorizer
        authorizer = apigw.CognitoUserPoolsAuthorizer(
            self,
            "CognitoAuthorizer",
            cognito_user_pools=[user_pool],
        )

        # Routes
        conversation_resource = api.root.add_resource("conversation")
        conversation_resource.add_method(
            "POST",
            apigw.LambdaIntegration(self.conversation_fn),
            authorizer=authorizer,
            authorization_type=apigw.AuthorizationType.COGNITO,
        )

        conversations_resource = api.root.add_resource("conversations")
        conversations_resource.add_method(
            "GET",
            apigw.LambdaIntegration(self.conversation_fn),
            authorizer=authorizer,
            authorization_type=apigw.AuthorizationType.COGNITO,
        )

        # GET /capabilities — session-start probe (#171 phase C).
        capabilities_resource = api.root.add_resource("capabilities")
        capabilities_resource.add_method(
            "GET",
            apigw.LambdaIntegration(self.capabilities_fn),
            authorizer=authorizer,
            authorization_type=apigw.AuthorizationType.COGNITO,
        )

        # ------------------------------------------------------------------
        # Download endpoint. Cognito-authed proxy download for artifacts in
        # the reports bucket. Replaces the S3 presigned URLs that broke on
        # role-chained STS session tokens with boto3 >= 1.42.97 (S3
        # returned InvalidToken on well-formed URLs; verified via
        # head_object probes that the underlying creds were valid).
        #
        # The frontend intercepts clicks on `/downloads/` links and fetches
        # with the current Cognito Bearer token — direct browser navigation
        # to a Cognito-authed URL doesn't work (browsers can't attach auth
        # headers on navigation), so the fetch-and-blob pattern is the
        # standard authenticated-download design here.
        # ------------------------------------------------------------------
        self.download_fn = lambda_.Function(
            self,
            "DownloadHandler",
            runtime=lambda_.Runtime.PYTHON_3_14,
            handler="download.handler",
            code=lambda_.Code.from_asset(src_path),
            timeout=Duration.seconds(30),
            memory_size=512,
            environment={
                "REPORTS_BUCKET": reports_bucket.bucket_name,
            },
        )
        # Read-only bucket access — GetObject / HeadObject. No List (key
        # known upfront), no Write, no AssumeRole. Prefix allowlist and
        # path-traversal guards are enforced in code, not IAM, so a
        # future prefix change doesn't require a stack update.
        reports_bucket.grant_read(self.download_fn)

        # GET /downloads/{proxy+} — Cognito-authed proxy path.
        downloads_resource = api.root.add_resource("downloads")
        download_proxy = downloads_resource.add_resource("{proxy+}")
        download_proxy.add_method(
            "GET",
            apigw.LambdaIntegration(self.download_fn),
            authorizer=authorizer,
            authorization_type=apigw.AuthorizationType.COGNITO,
        )

        self.api_endpoint = api.url

        # Thread the API endpoint into export_report and list_exports so
        # they can generate `download_url` values pointing at /downloads/.
        #
        # CIRCULAR DEPENDENCY NOTE: `api.url` resolves to a token that
        # depends on the API GW Deployment/Stage. The Deployment depends
        # on every Method, and the Methods depend on the Lambda
        # integrations (ConversationHandler, DownloadHandler, ...). The
        # ConversationHandler's role has `grant_invoke` on these two
        # tool Lambdas, so making these tool Lambdas depend on `api.url`
        # closes a cycle:
        #
        #   ExportReport → api.url → Stage → Deployment → Methods
        #     → ConversationHandler → ConvHandler role policy
        #     → grant_invoke on ExportReport → ExportReport
        #
        # Constructing the URL from `api.rest_api_id` (a token that
        # depends ONLY on the RestApi resource itself, not on the
        # deployment tree) breaks the cycle. `${AWS::Region}` is a
        # CloudFormation intrinsic resolved at deploy time. Stage name is
        # hardcoded to "prod" — the CDK default that this stack uses. A
        # customer who overrides the stage name would need to update
        # this literal too.
        api_endpoint_for_tools = Fn.sub(
            "https://${ApiId}.execute-api.${AWS::Region}.amazonaws.com/prod/",
            {"ApiId": api.rest_api_id},
        )
        for tool_name in ("export_report", "list_exports"):
            tool_fn = tools_functions.get(tool_name)
            if tool_fn is not None:
                tool_fn.add_environment("API_ENDPOINT", api_endpoint_for_tools)
