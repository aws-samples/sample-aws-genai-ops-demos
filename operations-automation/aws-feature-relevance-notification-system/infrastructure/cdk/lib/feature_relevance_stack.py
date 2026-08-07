"""AWS Feature Relevance Notification System - CDK Stack."""
import os

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_kms as kms,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
    aws_sqs as sqs,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
)
from constructs import Construct


class FeatureRelevanceStack(Stack):
    """Stack for the AWS Feature Relevance Notification System."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Context values (overridable via cdk.json or --context)
        bedrock_model_id = self.node.try_get_context("bedrock_model_id") or "us.anthropic.claude-sonnet-4-6"
        knowledge_base_id = self.node.try_get_context("knowledge_base_id") or ""
        slack_webhook_url = self.node.try_get_context("slack_webhook_url") or ""

        # ============================================================
        # KMS Customer Managed Key
        # ============================================================
        encryption_key = kms.Key(
            self,
            "EncryptionKey",
            description="CMK for feature-relevance resources",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ============================================================
        # DynamoDB Tables
        # ============================================================
        workload_profiles_table = dynamodb.Table(
            self,
            "WorkloadProfilesTable",
            partition_key=dynamodb.Attribute(
                name="workload_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=encryption_key,
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        announcement_state_table = dynamodb.Table(
            self,
            "AnnouncementStateTable",
            partition_key=dynamodb.Attribute(
                name="announcement_url", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=encryption_key,
            point_in_time_recovery=True,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ============================================================
        # Secrets Manager
        # ============================================================
        slack_webhook_secret = secretsmanager.Secret(
            self,
            "SlackWebhookSecret",
            description="Slack webhook URL for feature relevance notifications",
            encryption_key=encryption_key,
            secret_string_beta1=secretsmanager.SecretStringValueBeta1.from_unsafe_plaintext(
                f'{{"webhook_url":"{slack_webhook_url}"}}'
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ============================================================
        # Dead Letter Queue
        # ============================================================
        dlq = sqs.Queue(
            self,
            "LambdaDLQ",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=encryption_key,
        )

        # ============================================================
        # Lambda Functions
        # ============================================================
        lambdas_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "lambdas")

        # Common Lambda properties
        common_lambda_props = {
            "runtime": lambda_.Runtime.PYTHON_3_12,
            "architecture": lambda_.Architecture.ARM_64,
            "timeout": Duration.seconds(120),
            "memory_size": 256,
            "dead_letter_queue": dlq,
            "log_retention": logs.RetentionDays.TWO_WEEKS,
        }

        # RSS Ingestion Lambda
        rss_ingestion_fn = lambda_.Function(
            self,
            "RSSIngestionFunction",
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset(os.path.join(lambdas_dir, "rss-ingestion")),
            reserved_concurrent_executions=5,
            environment={
                "STATE_TABLE": announcement_state_table.table_name,
                "PROFILES_TABLE": workload_profiles_table.table_name,
            },
            **common_lambda_props,
        )

        # Relevance Scorer Lambda
        relevance_scorer_fn = lambda_.Function(
            self,
            "RelevanceScorerFunction",
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset(os.path.join(lambdas_dir, "relevance-scorer")),
            timeout=Duration.seconds(60),
            reserved_concurrent_executions=10,
            environment={
                "MODEL_ID": bedrock_model_id,
                "KNOWLEDGE_BASE_ID": knowledge_base_id,
            },
            **{k: v for k, v in common_lambda_props.items() if k != "timeout"},
        )

        # Slack Notification Lambda
        slack_notification_fn = lambda_.Function(
            self,
            "SlackNotificationFunction",
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset(os.path.join(lambdas_dir, "slack-notification")),
            timeout=Duration.seconds(30),
            memory_size=128,
            reserved_concurrent_executions=5,
            environment={
                "SLACK_SECRET_ARN": slack_webhook_secret.secret_arn,
            },
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            dead_letter_queue=dlq,
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )

        # Workload Manager Lambda
        workload_manager_fn = lambda_.Function(
            self,
            "WorkloadManagerFunction",
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset(os.path.join(lambdas_dir, "workload-manager")),
            timeout=Duration.seconds(30),
            memory_size=128,
            reserved_concurrent_executions=5,
            environment={
                "PROFILES_TABLE": workload_profiles_table.table_name,
            },
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            dead_letter_queue=dlq,
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )

        # ============================================================
        # IAM Permissions
        # ============================================================

        # DynamoDB access
        workload_profiles_table.grant_read_write_data(rss_ingestion_fn)
        announcement_state_table.grant_read_write_data(rss_ingestion_fn)
        workload_profiles_table.grant_read_write_data(workload_manager_fn)

        # Secrets Manager access
        slack_webhook_secret.grant_read(slack_notification_fn)

        # KMS access for all Lambdas
        encryption_key.grant_decrypt(rss_ingestion_fn)
        encryption_key.grant_decrypt(relevance_scorer_fn)
        encryption_key.grant_decrypt(slack_notification_fn)
        encryption_key.grant_decrypt(workload_manager_fn)

        # Bedrock access for scorer (wildcard region needed for cross-region inference profiles)
        relevance_scorer_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/*",
                ],
            )
        )

        # Knowledge Base access (conditional)
        if knowledge_base_id:
            relevance_scorer_fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["bedrock:Retrieve", "bedrock:RetrieveAndGenerate"],
                    resources=[
                        f"arn:aws:bedrock:{self.region}:{self.account}:knowledge-base/{knowledge_base_id}"
                    ],
                )
            )

        # ============================================================
        # Step Functions State Machine
        # ============================================================

        # Score Relevance task
        score_relevance = tasks.LambdaInvoke(
            self,
            "ScoreRelevance",
            lambda_function=relevance_scorer_fn,
            result_selector={"scoring.$": "$.Payload"},
            result_path="$.scoringResult",
            retry_on_service_exceptions=True,
        )
        score_relevance.add_retry(
            errors=["Lambda.ServiceException", "Lambda.TooManyRequestsException"],
            interval=Duration.seconds(5),
            max_attempts=3,
            backoff_rate=2,
        )

        # Send Slack Notification task
        send_notification = tasks.LambdaInvoke(
            self,
            "SendSlackNotification",
            lambda_function=slack_notification_fn,
            payload=sfn.TaskInput.from_object(
                {
                    "announcement.$": "$.announcement",
                    "scoring.$": "$.scoringResult.scoring",
                }
            ),
            result_path="$.notificationResult",
        )
        send_notification.add_retry(
            errors=["Lambda.ServiceException"],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2,
        )

        # Below threshold - skip
        below_threshold = sfn.Pass(
            self,
            "BelowThreshold",
            result=sfn.Result.from_object(
                {"status": "skipped", "reason": "Score below threshold"}
            ),
        )

        # Scoring failed
        scoring_failed = sfn.Pass(
            self,
            "ScoringFailed",
            result=sfn.Result.from_object(
                {"status": "error", "reason": "Scoring Lambda failed"}
            ),
        )

        # Add error catch to scoring
        score_relevance.add_catch(scoring_failed, result_path="$.error")

        # Threshold choice
        check_threshold = sfn.Choice(self, "CheckRelevanceThreshold")

        # Tier-0 condition (lower threshold of 25)
        tier_0_condition = sfn.Condition.and_(
            sfn.Condition.string_equals("$.workload.workload_tier", "tier-0"),
            sfn.Condition.number_greater_than_equals(
                "$.scoringResult.scoring.relevance_score", 25
            ),
        )

        # Standard threshold (40)
        standard_condition = sfn.Condition.number_greater_than_equals(
            "$.scoringResult.scoring.relevance_score", 40
        )

        # Build state machine definition
        definition = score_relevance.next(
            check_threshold.when(tier_0_condition, send_notification)
            .when(standard_condition, send_notification)
            .otherwise(below_threshold)
        )

        state_machine = sfn.StateMachine(
            self,
            "FeatureRelevanceStateMachine",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.minutes(5),
        )

        # Grant RSS Lambda permission to start executions
        state_machine.grant_start_execution(rss_ingestion_fn)

        # Add state machine ARN to RSS Lambda environment
        rss_ingestion_fn.add_environment(
            "STATE_MACHINE_ARN", state_machine.state_machine_arn
        )

        # ============================================================
        # EventBridge Schedule (daily RSS polling)
        # ============================================================
        events.Rule(
            self,
            "DailyRSSPolling",
            schedule=events.Schedule.rate(Duration.days(1)),
            targets=[targets.LambdaFunction(rss_ingestion_fn)],
        )

        # ============================================================
        # Stack Outputs
        # ============================================================
        CfnOutput(self, "RSSIngestionFunctionName",
                  value=rss_ingestion_fn.function_name,
                  description="RSS Ingestion Lambda function name")

        CfnOutput(self, "WorkloadManagerFunctionName",
                  value=workload_manager_fn.function_name,
                  description="Workload Manager Lambda function name")

        CfnOutput(self, "SlackSecretArn",
                  value=slack_webhook_secret.secret_arn,
                  description="Secrets Manager ARN for Slack webhook")

        CfnOutput(self, "StateMachineArn",
                  value=state_machine.state_machine_arn,
                  description="Step Functions State Machine ARN")

        CfnOutput(self, "WorkloadProfilesTableName",
                  value=workload_profiles_table.table_name,
                  description="DynamoDB table for workload profiles")
