"""VPN Demo Stack — 2 VPCs, EC2 instances, Site-to-Site VPN, alarms, webhook Lambda."""
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_logs as logs,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
    aws_iam as iam,
    aws_lambda as lambda_,
    CfnOutput,
    Fn,
)
from constructs import Construct


class VpnDemoStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        key_pair_name: str,
        routing_type: str,
        webhook_url: str,
        webhook_secret: str,
        ssh_cidr: str = "0.0.0.0/0",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        is_static = routing_type == "static"
        has_webhook = bool(webhook_url)

        # AZ fix — select first AZ to avoid t3.micro unavailability (e.g. us-east-1e)
        az = Fn.select(0, Fn.get_azs(""))

        # ============ Cloud VPC (10.0.0.0/16) ============
        cloud_vpc = ec2.CfnVPC(self, "CloudVpc",
            cidr_block="10.0.0.0/16",
            enable_dns_support=True,
            enable_dns_hostnames=True,
            tags=[cdk.CfnTag(key="Name", value="vpn-demo-cloud-vpc")],
        )

        cloud_igw = ec2.CfnInternetGateway(self, "CloudIgw")
        ec2.CfnVPCGatewayAttachment(self, "CloudIgwAttach",
            vpc_id=cloud_vpc.ref, internet_gateway_id=cloud_igw.ref,
        )

        cloud_subnet = ec2.CfnSubnet(self, "CloudSubnet",
            vpc_id=cloud_vpc.ref,
            cidr_block="10.0.1.0/24",
            availability_zone=az,
            map_public_ip_on_launch=True,
            tags=[cdk.CfnTag(key="Name", value="vpn-demo-cloud-subnet")],
        )

        cloud_rt = ec2.CfnRouteTable(self, "CloudRouteTable",
            vpc_id=cloud_vpc.ref,
        )
        ec2.CfnRoute(self, "CloudDefaultRoute",
            route_table_id=cloud_rt.ref,
            destination_cidr_block="0.0.0.0/0",
            gateway_id=cloud_igw.ref,
        ).add_dependency(
            self.node.find_child("CloudIgwAttach")
        )
        ec2.CfnSubnetRouteTableAssociation(self, "CloudSubnetRtAssoc",
            subnet_id=cloud_subnet.ref, route_table_id=cloud_rt.ref,
        )

        cloud_sg = ec2.CfnSecurityGroup(self, "CloudSg",
            group_description="Cloud VPC - allow ICMP from on-prem + SSH",
            vpc_id=cloud_vpc.ref,
            security_group_ingress=[
                {"ipProtocol": "icmp", "fromPort": -1, "toPort": -1, "cidrIp": "172.16.0.0/16"},
                {"ipProtocol": "tcp", "fromPort": 22, "toPort": 22, "cidrIp": ssh_cidr},
            ],
        )

        cloud_instance = ec2.CfnInstance(self, "CloudInstance",
            instance_type="t3.micro",
            image_id="{{resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64}}",
            key_name=key_pair_name,
            subnet_id=cloud_subnet.ref,
            security_group_ids=[cloud_sg.ref],
            tags=[cdk.CfnTag(key="Name", value="vpn-demo-cloud-instance")],
        )

        # ============ On-Prem VPC (172.16.0.0/16) ============
        onprem_vpc = ec2.CfnVPC(self, "OnPremVpc",
            cidr_block="172.16.0.0/16",
            enable_dns_support=True,
            enable_dns_hostnames=True,
            tags=[cdk.CfnTag(key="Name", value="vpn-demo-onprem-vpc")],
        )

        onprem_igw = ec2.CfnInternetGateway(self, "OnPremIgw")
        ec2.CfnVPCGatewayAttachment(self, "OnPremIgwAttach",
            vpc_id=onprem_vpc.ref, internet_gateway_id=onprem_igw.ref,
        )

        onprem_subnet = ec2.CfnSubnet(self, "OnPremSubnet",
            vpc_id=onprem_vpc.ref,
            cidr_block="172.16.1.0/24",
            availability_zone=az,
            map_public_ip_on_launch=True,
            tags=[cdk.CfnTag(key="Name", value="vpn-demo-onprem-subnet")],
        )

        onprem_rt = ec2.CfnRouteTable(self, "OnPremRouteTable",
            vpc_id=onprem_vpc.ref,
        )
        ec2.CfnRoute(self, "OnPremDefaultRoute",
            route_table_id=onprem_rt.ref,
            destination_cidr_block="0.0.0.0/0",
            gateway_id=onprem_igw.ref,
        ).add_dependency(
            self.node.find_child("OnPremIgwAttach")
        )
        ec2.CfnSubnetRouteTableAssociation(self, "OnPremSubnetRtAssoc",
            subnet_id=onprem_subnet.ref, route_table_id=onprem_rt.ref,
        )

        onprem_sg = ec2.CfnSecurityGroup(self, "OnPremSg",
            group_description="On-prem VPC - IKE + SSH + ICMP",
            vpc_id=onprem_vpc.ref,
            security_group_ingress=[
                {"ipProtocol": "icmp", "fromPort": -1, "toPort": -1, "cidrIp": "10.0.0.0/16"},
                {"ipProtocol": "tcp", "fromPort": 22, "toPort": 22, "cidrIp": ssh_cidr},
                {"ipProtocol": "udp", "fromPort": 500, "toPort": 500, "cidrIp": "0.0.0.0/0"},
                {"ipProtocol": "udp", "fromPort": 4500, "toPort": 4500, "cidrIp": "0.0.0.0/0"},
            ],
        )

        cgw_eip = ec2.CfnEIP(self, "CgwEip", domain="vpc")

        userdata = ec2.UserData.for_linux()
        userdata.add_commands(
            "exec > /var/log/vpn-userdata.log 2>&1",
            "set -e",
            "yum clean packages",
            "yum install -y libreswan iptables-nft iproute-tc",
            "ipsec initnss 2>/dev/null || true",
            "curl -sL https://github.com/osrg/gobgp/releases/download/v3.30.0/gobgp_3.30.0_linux_amd64.tar.gz | tar xz -C /usr/local/bin",
            "cat > /etc/sysctl.d/99-vpn.conf <<'EOF'",
            "net.ipv4.ip_forward = 1",
            "net.ipv4.conf.all.rp_filter = 0",
            "net.ipv4.conf.default.rp_filter = 0",
            "EOF",
            "sysctl -p /etc/sysctl.d/99-vpn.conf",
            "echo 'USERDATA_COMPLETE'",
        )

        cgw_instance = ec2.CfnInstance(self, "CgwInstance",
            instance_type="t3.micro",
            image_id="{{resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64}}",
            key_name=key_pair_name,
            subnet_id=onprem_subnet.ref,
            security_group_ids=[onprem_sg.ref],
            source_dest_check=False,
            user_data=Fn.base64(userdata.render()),
            tags=[cdk.CfnTag(key="Name", value="vpn-demo-cgw")],
        )

        ec2.CfnEIPAssociation(self, "CgwEipAssoc",
            allocation_id=cgw_eip.attr_allocation_id,
            instance_id=cgw_instance.ref,
        )

        # ============ VPN ============
        vgw = ec2.CfnVPNGateway(self, "Vgw",
            type="ipsec.1",
            tags=[cdk.CfnTag(key="Name", value="vpn-demo-vgw")],
        )
        vgw_attach = ec2.CfnVPCGatewayAttachment(self, "VgwAttach",
            vpc_id=cloud_vpc.ref, vpn_gateway_id=vgw.ref,
        )

        # Route cloud → on-prem via VGW (depends on VGW attachment)
        cloud_to_onprem = ec2.CfnRoute(self, "CloudToOnpremRoute",
            route_table_id=cloud_rt.ref,
            destination_cidr_block="172.16.0.0/16",
            gateway_id=vgw.ref,
        )
        cloud_to_onprem.add_dependency(vgw_attach)

        cgw_resource = ec2.CfnCustomerGateway(self, "Cgw",
            type="ipsec.1",
            bgp_asn=65000,
            ip_address=cgw_eip.ref,
            tags=[cdk.CfnTag(key="Name", value="vpn-demo-cgw")],
        )
        cgw_resource.add_dependency(
            self.node.find_child("CgwEipAssoc")
        )

        vpn_log_group = logs.LogGroup(self, "VpnLogGroup",
            log_group_name="/vpn-demo/tunnel-logs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        tunnel_log_opts = {
            "cloudwatchLogOptions": {
                "logEnabled": True,
                "logGroupArn": vpn_log_group.log_group_arn,
                "logOutputFormat": "json",
            }
        }

        vpn_connection = ec2.CfnVPNConnection(self, "VpnConnection",
            type="ipsec.1",
            customer_gateway_id=cgw_resource.ref,
            vpn_gateway_id=vgw.ref,
            static_routes_only=is_static,
            vpn_tunnel_options_specifications=[
                {"tunnelInsideCidr": "169.254.10.0/30", "logOptions": tunnel_log_opts},
                {"tunnelInsideCidr": "169.254.10.4/30", "logOptions": tunnel_log_opts},
            ],
            tags=[cdk.CfnTag(key="Name", value="vpn-demo-connection")],
        )
        vpn_connection.add_dependency(vpn_log_group.node.default_child)

        # CDK 2.178.2 doesn't include BgpLogEnabled in its type definitions,
        # so we add it via CFN override to ensure BGP logs are enabled.
        vpn_connection.add_property_override(
            "VpnTunnelOptionsSpecifications.0.LogOptions.CloudwatchLogOptions.BgpLogEnabled", True)
        vpn_connection.add_property_override(
            "VpnTunnelOptionsSpecifications.0.LogOptions.CloudwatchLogOptions.BgpLogGroupArn",
            vpn_log_group.log_group_arn)
        vpn_connection.add_property_override(
            "VpnTunnelOptionsSpecifications.0.LogOptions.CloudwatchLogOptions.BgpLogOutputFormat", "json")
        vpn_connection.add_property_override(
            "VpnTunnelOptionsSpecifications.1.LogOptions.CloudwatchLogOptions.BgpLogEnabled", True)
        vpn_connection.add_property_override(
            "VpnTunnelOptionsSpecifications.1.LogOptions.CloudwatchLogOptions.BgpLogGroupArn",
            vpn_log_group.log_group_arn)
        vpn_connection.add_property_override(
            "VpnTunnelOptionsSpecifications.1.LogOptions.CloudwatchLogOptions.BgpLogOutputFormat", "json")

        if is_static:
            ec2.CfnVPNConnectionRoute(self, "VpnStaticRoute",
                vpn_connection_id=vpn_connection.ref,
                destination_cidr_block="172.16.0.0/16",
            )

        # ============ Monitoring ============
        alarm_topic = sns.Topic(self, "AlarmSnsTopic",
            topic_name="vpn-demo-tunnel-alarm",
        )

        # ============ Webhook Lambda (conditional) ============
        if has_webhook:
            webhook_role = iam.Role(self, "WebhookLambdaRole",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name(
                        "service-role/AWSLambdaBasicExecutionRole"
                    ),
                ],
            )

            webhook_fn = lambda_.Function(self, "WebhookLambda",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler="index.handler",
                timeout=cdk.Duration.seconds(30),
                role=webhook_role,
                environment={
                    "WEBHOOK_URL": webhook_url,
                    "WEBHOOK_SECRET": webhook_secret,
                },
                code=lambda_.Code.from_inline(
                    'import json, os, hmac, hashlib, base64, urllib.request\n'
                    'from datetime import datetime, timezone\n'
                    'def handler(event, context):\n'
                    '    message = event["Records"][0]["Sns"]["Message"]\n'
                    '    timestamp = datetime.now(timezone.utc).isoformat()\n'
                    '    payload = json.dumps({\n'
                    '        "eventType": "incident", "incidentId": context.aws_request_id,\n'
                    '        "action": "created", "priority": "HIGH",\n'
                    '        "title": "VPN Tunnel Alert", "description": message,\n'
                    '        "service": "AWS-VPN", "timestamp": timestamp,\n'
                    '        "data": {"rawMessage": message}\n'
                    '    })\n'
                    '    secret = os.environ["WEBHOOK_SECRET"]\n'
                    '    sig = base64.b64encode(hmac.new(\n'
                    '        secret.encode(), f"{timestamp}:{payload}".encode(), hashlib.sha256\n'
                    '    ).digest()).decode()\n'
                    '    req = urllib.request.Request(os.environ["WEBHOOK_URL"], data=payload.encode(), headers={\n'
                    '        "Content-Type": "application/json",\n'
                    '        "x-amzn-event-timestamp": timestamp, "x-amzn-event-signature": sig\n'
                    '    })\n'
                    '    urllib.request.urlopen(req)\n'
                ),
            )

            alarm_topic.add_subscription(subs.LambdaSubscription(webhook_fn))

        # ============ Outputs ============
        CfnOutput(self, "VpnConnectionId", value=vpn_connection.ref)
        CfnOutput(self, "CgwInstanceId", value=cgw_instance.ref)
        CfnOutput(self, "CgwPublicIp", value=cgw_eip.ref)
        CfnOutput(self, "CloudInstanceId", value=cloud_instance.ref)
        CfnOutput(self, "CloudInstancePrivateIp", value=cloud_instance.attr_private_ip)
        CfnOutput(self, "VpnLogGroupName", value=vpn_log_group.log_group_name)
        CfnOutput(self, "VpnLogGroupArn", value=vpn_log_group.log_group_arn)
        CfnOutput(self, "AlarmSnsTopicArn", value=alarm_topic.topic_arn)
        CfnOutput(self, "RoutingType", value=routing_type)
