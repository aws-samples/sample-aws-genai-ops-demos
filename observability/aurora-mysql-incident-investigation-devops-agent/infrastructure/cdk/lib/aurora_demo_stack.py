"""Aurora Demo Stack — Aurora MySQL cluster, bastion, alarms, failover events, webhook Lambda."""
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_events as events,
    aws_events_targets as targets,
    CfnOutput,
    Duration,
    RemovalPolicy,
)
from constructs import Construct

# Fixed identifiers so alarm dimensions and inject scripts are deterministic
CLUSTER_ID = "aurora-demo-cluster"
WRITER_ID = "aurora-demo-writer"
READER_ID = "aurora-demo-reader"
DEFAULT_DB = "appdb"


class AuroraDemoStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        key_pair_name: str,
        webhook_url: str,
        webhook_secret: str,
        ssh_cidr: str = "0.0.0.0/0",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        has_webhook = bool(webhook_url)

        # ============ Network (no NAT — bastion is public, DB is isolated) ============
        vpc = ec2.Vpc(
            self,
            "AuroraDemoVpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="db", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24
                ),
            ],
        )

        bastion_sg = ec2.SecurityGroup(
            self,
            "BastionSg",
            vpc=vpc,
            description="Aurora demo bastion/load-generator - SSH in",
            allow_all_outbound=True,
        )
        bastion_sg.add_ingress_rule(
            ec2.Peer.ipv4(ssh_cidr), ec2.Port.tcp(22), "SSH access"
        )

        db_sg = ec2.SecurityGroup(
            self,
            "DbSg",
            vpc=vpc,
            description="Aurora demo cluster - MySQL from bastion only",
            allow_all_outbound=True,
        )
        db_sg.add_ingress_rule(
            bastion_sg, ec2.Port.tcp(3306), "MySQL from bastion"
        )

        # ============ Aurora MySQL cluster ============
        # Version pinned via .of() to stay resilient across CDK library versions.
        engine = rds.DatabaseClusterEngine.aurora_mysql(
            version=rds.AuroraMysqlEngineVersion.of("8.0.mysql_aurora.3.08.0", "8.0")
        )

        # Graviton R-class: supports Performance Insights (burstable t3/t4g do not)
        # and demonstrates a cost-efficient Graviton default.
        instance_type = ec2.InstanceType.of(
            ec2.InstanceClass.MEMORY6_GRAVITON, ec2.InstanceSize.LARGE
        )

        cluster = rds.DatabaseCluster(
            self,
            "AuroraCluster",
            engine=engine,
            cluster_identifier=CLUSTER_ID,
            credentials=rds.Credentials.from_generated_secret(
                "admin", secret_name="aurora-demo/credentials"
            ),
            default_database_name=DEFAULT_DB,
            writer=rds.ClusterInstance.provisioned(
                "writer",
                instance_identifier=WRITER_ID,
                instance_type=instance_type,
                enable_performance_insights=True,
                publicly_accessible=False,
            ),
            readers=[
                rds.ClusterInstance.provisioned(
                    "reader",
                    instance_identifier=READER_ID,
                    instance_type=instance_type,
                    enable_performance_insights=True,
                    publicly_accessible=False,
                )
            ],
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[db_sg],
            storage_encrypted=True,
            cloudwatch_logs_exports=["error", "slowquery"],
            deletion_protection=False,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ============ Bastion / load-generator ============
        bastion_role = iam.Role(
            self,
            "BastionRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                )
            ],
        )
        cluster.secret.grant_read(bastion_role)
        bastion_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["rds:DescribeDBClusters", "rds:FailoverDBCluster"],
                resources=["*"],
            )
        )

        bastion_userdata = ec2.UserData.for_linux()
        bastion_userdata.add_commands(
            "exec > /var/log/aurora-userdata.log 2>&1",
            "set -x",
            "dnf clean all || true",
            "dnf install -y mariadb105 jq || yum install -y mariadb105 jq",
            "mkdir -p /opt/aurora-demo",
            "echo 'USERDATA_COMPLETE'",
        )

        bastion = ec2.Instance(
            self,
            "Bastion",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.MICRO
            ),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            security_group=bastion_sg,
            role=bastion_role,
            key_pair=ec2.KeyPair.from_key_pair_name(self, "KeyPair", key_pair_name),
            user_data=bastion_userdata,
        )

        # ============ Alerting fan-in ============
        alarm_topic = sns.Topic(self, "AlarmSnsTopic", topic_name="aurora-demo-alarm")

        # Allow CloudWatch alarms to publish to the topic. Required because the
        # EventBridge failover rule below attaches an explicit topic policy
        # (events.amazonaws.com), which overrides SNS's default policy and would
        # otherwise lock CloudWatch out, causing alarm actions to fail silently.
        alarm_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudWatchAlarmsPublish",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("cloudwatch.amazonaws.com")],
                actions=["sns:Publish"],
                resources=[alarm_topic.topic_arn],
            )
        )

        if has_webhook:
            webhook_role = iam.Role(
                self,
                "WebhookLambdaRole",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "service-role/AWSLambdaBasicExecutionRole"
                    )
                ],
            )

            webhook_fn = lambda_.Function(
                self,
                "WebhookLambda",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler="index.handler",
                timeout=Duration.seconds(30),
                role=webhook_role,
                environment={
                    "WEBHOOK_URL": webhook_url,
                    "WEBHOOK_SECRET": webhook_secret,
                },
                code=lambda_.Code.from_inline(
                    "import json, os, hmac, hashlib, base64, urllib.request\n"
                    "from datetime import datetime, timezone\n"
                    "def handler(event, context):\n"
                    "    message = event['Records'][0]['Sns']['Message']\n"
                    "    subject = event['Records'][0]['Sns'].get('Subject') or 'Aurora MySQL Alert'\n"
                    "    timestamp = datetime.now(timezone.utc).isoformat()\n"
                    "    payload = json.dumps({\n"
                    "        'eventType': 'incident', 'incidentId': context.aws_request_id,\n"
                    "        'action': 'created', 'priority': 'HIGH',\n"
                    "        'title': subject, 'description': message,\n"
                    "        'service': 'Amazon-Aurora-MySQL', 'timestamp': timestamp,\n"
                    "        'data': {'rawMessage': message}\n"
                    "    })\n"
                    "    secret = os.environ['WEBHOOK_SECRET']\n"
                    "    sig = base64.b64encode(hmac.new(\n"
                    "        secret.encode(), f'{timestamp}:{payload}'.encode(), hashlib.sha256\n"
                    "    ).digest()).decode()\n"
                    "    req = urllib.request.Request(os.environ['WEBHOOK_URL'], data=payload.encode(), headers={\n"
                    "        'Content-Type': 'application/json',\n"
                    "        'x-amzn-event-timestamp': timestamp, 'x-amzn-event-signature': sig\n"
                    "    })\n"
                    "    urllib.request.urlopen(req)\n"
                ),
            )

            alarm_topic.add_subscription(subs.LambdaSubscription(webhook_fn))

        # ============ CloudWatch alarms on RDS metrics ============
        def rds_metric(metric_name, instance_id, statistic="Average", period_min=1):
            return cloudwatch.Metric(
                namespace="AWS/RDS",
                metric_name=metric_name,
                dimensions_map={"DBInstanceIdentifier": instance_id},
                statistic=statistic,
                period=Duration.minutes(period_min),
            )

        sns_action = cw_actions.SnsAction(alarm_topic)

        # --- Core alarms (always active) ---
        connections_alarm = cloudwatch.Alarm(
            self,
            "ConnectionsHighAlarm",
            alarm_name="aurora-demo-connections-high",
            metric=rds_metric("DatabaseConnections", WRITER_ID, "Maximum"),
            threshold=150,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Aurora writer connection count is abnormally high (possible connection storm / pool leak).",
        )
        connections_alarm.add_alarm_action(sns_action)

        cpu_alarm = cloudwatch.Alarm(
            self,
            "CpuHighAlarm",
            alarm_name="aurora-demo-cpu-high",
            metric=rds_metric("CPUUtilization", WRITER_ID, "Average"),
            threshold=80,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Aurora writer CPU utilization is high (heavy or unbounded query workload).",
        )
        cpu_alarm.add_alarm_action(sns_action)

        deadlock_alarm = cloudwatch.Alarm(
            self,
            "DeadlocksAlarm",
            alarm_name="aurora-demo-deadlocks",
            # RDS publishes Deadlocks as an average-per-second rate; idle is 0.0 and
            # the deadlock injector drives it to ~0.9. A 0.5 threshold cleanly
            # separates an active deadlock storm from a quiet database.
            metric=rds_metric("Deadlocks", WRITER_ID, "Sum"),
            threshold=0.5,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Elevated InnoDB deadlock rate on the Aurora writer.",
        )
        deadlock_alarm.add_alarm_action(sns_action)

        # --- Dedicated alarms (actions disabled by deploy-all; enabled per scenario) ---
        memory_alarm = cloudwatch.Alarm(
            self,
            "FreeableMemoryLowAlarm",
            alarm_name="aurora-demo-memory-pressure",
            metric=rds_metric("FreeableMemory", WRITER_ID, "Minimum"),
            threshold=300_000_000,  # 300 MB
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Aurora writer freeable memory is low (large sorts / temp tables / memory pressure).",
        )
        memory_alarm.add_alarm_action(sns_action)

        replica_lag_alarm = cloudwatch.Alarm(
            self,
            "ReplicaLagAlarm",
            alarm_name="aurora-demo-replica-lag",
            metric=rds_metric("AuroraReplicaLag", READER_ID, "Average"),
            threshold=500,  # ms
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="Aurora reader replica lag exceeds threshold (read scaling / stale reads risk).",
        )
        replica_lag_alarm.add_alarm_action(sns_action)

        # ============ EventBridge — RDS failover / availability events → SNS ============
        # Failover: 0071 (started), 0072 (finished); 0025 (restarted); 0049 (multi-AZ failover complete)
        failover_rule = events.Rule(
            self,
            "RdsFailoverRule",
            rule_name="aurora-demo-failover-events",
            description="Route Aurora failover / availability events to the alarm topic",
            event_pattern=events.EventPattern(
                source=["aws.rds"],
                detail_type=["RDS DB Cluster Event", "RDS DB Instance Event"],
                detail={
                    "EventID": [
                        "RDS-EVENT-0071",
                        "RDS-EVENT-0072",
                        "RDS-EVENT-0025",
                        "RDS-EVENT-0049",
                    ]
                },
            ),
        )
        failover_rule.add_target(targets.SnsTopic(alarm_topic))

        # ============ Outputs ============
        CfnOutput(self, "ClusterIdentifier", value=CLUSTER_ID)
        CfnOutput(self, "WriterInstanceId", value=WRITER_ID)
        CfnOutput(self, "ReaderInstanceId", value=READER_ID)
        CfnOutput(self, "WriterEndpoint", value=cluster.cluster_endpoint.hostname)
        CfnOutput(self, "ReaderEndpoint", value=cluster.cluster_read_endpoint.hostname)
        CfnOutput(self, "DbPort", value="3306")
        CfnOutput(self, "DefaultDatabaseName", value=DEFAULT_DB)
        CfnOutput(self, "SecretArn", value=cluster.secret.secret_arn)
        CfnOutput(self, "BastionInstanceId", value=bastion.instance_id)
        CfnOutput(self, "BastionPublicIp", value=bastion.instance_public_ip)
        CfnOutput(self, "AlarmSnsTopicArn", value=alarm_topic.topic_arn)
