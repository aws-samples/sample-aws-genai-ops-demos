"""AWS architecture discovery module — read-only API calls to build an architecture profile."""

import argparse
import json
import os
from datetime import datetime, timezone

import boto3


def discover_vpcs(ec2):
    """Discover VPCs, subnets, internet gateways, and security groups."""
    vpcs = ec2.describe_vpcs().get("Vpcs", [])
    subnets = ec2.describe_subnets().get("Subnets", [])
    igws = ec2.describe_internet_gateways().get("InternetGateways", [])
    sgs = ec2.describe_security_groups().get("SecurityGroups", [])

    # Identify public subnets (associated with route table that has IGW route)
    route_tables = ec2.describe_route_tables().get("RouteTables", [])
    public_subnet_ids = set()
    for rt in route_tables:
        has_igw = any(
            r.get("GatewayId", "").startswith("igw-")
            for r in rt.get("Routes", [])
        )
        if has_igw:
            for assoc in rt.get("Associations", []):
                if assoc.get("SubnetId"):
                    public_subnet_ids.add(assoc["SubnetId"])

    # Flag risky security groups (0.0.0.0/0 ingress)
    risky_sgs = []
    for sg in sgs:
        for rule in sg.get("IpPermissions", []):
            for ip_range in rule.get("IpRanges", []):
                if ip_range.get("CidrIp") == "0.0.0.0/0":
                    risky_sgs.append({
                        "GroupId": sg["GroupId"],
                        "GroupName": sg.get("GroupName", ""),
                        "Port": rule.get("FromPort", "all"),
                    })
                    break

    return {
        "vpcs": [{"VpcId": v["VpcId"], "CidrBlock": v.get("CidrBlock")} for v in vpcs],
        "subnets_total": len(subnets),
        "public_subnets": list(public_subnet_ids),
        "internet_gateways": len(igws),
        "security_groups_total": len(sgs),
        "risky_security_groups": risky_sgs,
    }


def discover_compute(ec2, lambda_client, ecs, eks, region):
    """Discover EC2 instances, Lambda functions, ECS clusters, EKS clusters."""
    # EC2
    reservations = ec2.describe_instances().get("Reservations", [])
    instances = []
    for r in reservations:
        for i in r.get("Instances", []):
            instances.append({
                "InstanceId": i["InstanceId"],
                "InstanceType": i.get("InstanceType"),
                "State": i.get("State", {}).get("Name"),
                "PublicIp": i.get("PublicIpAddress"),
                "IamProfile": i.get("IamInstanceProfile", {}).get("Arn"),
            })

    # Lambda
    functions = []
    paginator = lambda_client.get_paginator("list_functions")
    for page in paginator.paginate():
        for fn in page.get("Functions", []):
            functions.append({
                "FunctionName": fn["FunctionName"],
                "Runtime": fn.get("Runtime"),
                "Role": fn.get("Role"),
                "VpcConfig": bool(fn.get("VpcConfig", {}).get("SubnetIds")),
            })

    # ECS
    ecs_clusters = []
    cluster_arns = ecs.list_clusters().get("clusterArns", [])
    if cluster_arns:
        details = ecs.describe_clusters(clusters=cluster_arns).get("clusters", [])
        for c in details:
            ecs_clusters.append({
                "ClusterName": c.get("clusterName"),
                "RunningTasks": c.get("runningTasksCount", 0),
                "ActiveServices": c.get("activeServicesCount", 0),
            })

    # EKS
    eks_clusters = []
    try:
        eks_names = eks.list_clusters().get("clusters", [])
        for name in eks_names:
            info = eks.describe_cluster(name=name).get("cluster", {})
            eks_clusters.append({
                "ClusterName": name,
                "Version": info.get("version"),
                "EndpointPublic": info.get("resourcesVpcConfig", {}).get("endpointPublicAccess"),
            })
    except Exception:
        pass  # EKS may not be used

    return {
        "ec2_instances": instances,
        "lambda_functions": functions,
        "ecs_clusters": ecs_clusters,
        "eks_clusters": eks_clusters,
    }


def discover_data_stores(s3, rds, dynamodb):
    """Discover S3 buckets, RDS instances, DynamoDB tables."""
    # S3
    buckets = []
    for b in s3.list_buckets().get("Buckets", []):
        name = b.get("BucketName") or b.get("Name", "")
        bucket_info = {"BucketName": name}
        try:
            acl = s3.get_bucket_acl(Bucket=name)
            public_grants = [
                g for g in acl.get("Grants", [])
                if g.get("Grantee", {}).get("URI", "").endswith("AllUsers")
                or g.get("Grantee", {}).get("URI", "").endswith("AuthenticatedUsers")
            ]
            bucket_info["PublicAcl"] = len(public_grants) > 0
        except Exception:
            bucket_info["PublicAcl"] = None
        try:
            pab = s3.get_public_access_block(Bucket=name).get("PublicAccessBlockConfiguration", {})
            bucket_info["PublicAccessBlocked"] = all(pab.values())
        except Exception:
            bucket_info["PublicAccessBlocked"] = False
        buckets.append(bucket_info)

    # RDS
    rds_instances = []
    for db in rds.describe_db_instances().get("DBInstances", []):
        rds_instances.append({
            "DBInstanceId": db["DBInstanceIdentifier"],
            "Engine": db.get("Engine"),
            "PubliclyAccessible": db.get("PubliclyAccessible"),
            "Encrypted": db.get("StorageEncrypted"),
            "MultiAZ": db.get("MultiAZ"),
        })

    # DynamoDB
    ddb_tables = []
    for table_name in dynamodb.list_tables().get("TableNames", []):
        info = dynamodb.describe_table(TableName=table_name).get("Table", {})
        ddb_tables.append({
            "TableName": table_name,
            "Encrypted": info.get("SSEDescription", {}).get("Status") == "ENABLED",
        })

    return {
        "s3_buckets": buckets,
        "rds_instances": rds_instances,
        "dynamodb_tables": ddb_tables,
    }


def discover_identity(iam):
    """Discover IAM roles, users, and access key age."""
    # Roles
    roles = []
    paginator = iam.get_paginator("list_roles")
    for page in paginator.paginate():
        for role in page.get("Roles", []):
            # Check for overprivileged managed policies
            attached = iam.list_attached_role_policies(RoleName=role["RoleName"]).get("AttachedPolicies", [])
            admin_policies = [p["PolicyName"] for p in attached if "Admin" in p["PolicyName"]]
            roles.append({
                "RoleName": role["RoleName"],
                "CreateDate": role.get("CreateDate", "").isoformat() if hasattr(role.get("CreateDate", ""), "isoformat") else str(role.get("CreateDate", "")),
                "AdminPolicies": admin_policies,
            })

    # Users and access keys
    users = []
    for user in iam.list_users().get("Users", []):
        keys = iam.list_access_keys(UserName=user["UserName"]).get("AccessKeyMetadata", [])
        active_keys = [k for k in keys if k.get("Status") == "Active"]
        users.append({
            "UserName": user["UserName"],
            "ActiveAccessKeys": len(active_keys),
        })

    return {
        "iam_roles": roles,
        "iam_users": users,
        "total_roles": len(roles),
        "total_users": len(users),
        "overprivileged_roles": [r["RoleName"] for r in roles if r["AdminPolicies"]],
        "long_lived_access_keys": sum(u["ActiveAccessKeys"] for u in users),
    }


def discover_endpoints(elbv2, apigw):
    """Discover ALBs, NLBs, and API Gateways."""
    # Load balancers
    lbs = []
    for lb in elbv2.describe_load_balancers().get("LoadBalancers", []):
        lbs.append({
            "Name": lb.get("LoadBalancerName"),
            "Type": lb.get("Type"),
            "Scheme": lb.get("Scheme"),  # internet-facing or internal
            "DNSName": lb.get("DNSName"),
        })

    # API Gateways
    apis = []
    try:
        for api in apigw.get_rest_apis().get("items", []):
            apis.append({
                "Name": api.get("name"),
                "Id": api.get("id"),
                "EndpointType": api.get("endpointConfiguration", {}).get("types", []),
            })
    except Exception:
        pass

    return {
        "load_balancers": lbs,
        "api_gateways": apis,
        "public_endpoints_count": (
            len([lb for lb in lbs if lb.get("Scheme") == "internet-facing"])
            + len(apis)
        ),
    }


def build_risk_indicators(network, compute, data_stores, identity, endpoints):
    """Summarize key risk indicators from all discovery data."""
    return {
        "public_endpoints_count": endpoints.get("public_endpoints_count", 0),
        "risky_security_groups_count": len(network.get("risky_security_groups", [])),
        "overprivileged_roles": identity.get("overprivileged_roles", []),
        "long_lived_access_keys": identity.get("long_lived_access_keys", 0),
        "public_s3_buckets": [
            b["BucketName"] for b in data_stores.get("s3_buckets", [])
            if b.get("PublicAcl") or not b.get("PublicAccessBlocked")
        ],
        "publicly_accessible_rds": [
            db["DBInstanceId"] for db in data_stores.get("rds_instances", [])
            if db.get("PubliclyAccessible")
        ],
        "unencrypted_rds": [
            db["DBInstanceId"] for db in data_stores.get("rds_instances", [])
            if not db.get("Encrypted")
        ],
        "ec2_with_public_ip": [
            i["InstanceId"] for i in compute.get("ec2_instances", [])
            if i.get("PublicIp")
        ],
        "eks_public_endpoints": [
            c["ClusterName"] for c in compute.get("eks_clusters", [])
            if c.get("EndpointPublic")
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Discover AWS architecture for IR playbook generation")
    parser.add_argument("--region", required=True, help="AWS region to scan")
    parser.add_argument("--output", required=True, help="Output path for architecture profile JSON")
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    account_id = session.client("sts").get_caller_identity()["Account"]

    print(f"  Scanning account {account_id} in {args.region}...")

    ec2 = session.client("ec2")
    lambda_client = session.client("lambda")
    ecs = session.client("ecs")
    eks = session.client("eks")
    s3 = session.client("s3")
    rds = session.client("rds")
    dynamodb = session.client("dynamodb")
    iam = session.client("iam")
    elbv2 = session.client("elbv2")
    apigw = session.client("apigateway")

    print("  Discovering network...")
    network = discover_vpcs(ec2)
    print("  Discovering compute...")
    compute = discover_compute(ec2, lambda_client, ecs, eks, args.region)
    print("  Discovering data stores...")
    data_stores = discover_data_stores(s3, rds, dynamodb)
    print("  Discovering identity...")
    identity = discover_identity(iam)
    print("  Discovering endpoints...")
    endpoints = discover_endpoints(elbv2, apigw)

    risk_indicators = build_risk_indicators(network, compute, data_stores, identity, endpoints)

    profile = {
        "account_id": account_id,
        "region": args.region,
        "scan_timestamp": datetime.now(timezone.utc).isoformat(),
        "network": network,
        "compute": compute,
        "data_stores": data_stores,
        "identity": identity,
        "endpoints": endpoints,
        "risk_indicators": risk_indicators,
    }

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(profile, f, indent=2, default=str)

    total_resources = (
        len(network.get("vpcs", []))
        + len(compute.get("ec2_instances", []))
        + len(compute.get("lambda_functions", []))
        + len(compute.get("ecs_clusters", []))
        + len(compute.get("eks_clusters", []))
        + len(data_stores.get("s3_buckets", []))
        + len(data_stores.get("rds_instances", []))
        + len(data_stores.get("dynamodb_tables", []))
        + identity.get("total_roles", 0)
        + identity.get("total_users", 0)
        + len(endpoints.get("load_balancers", []))
        + len(endpoints.get("api_gateways", []))
    )
    print(f"  Discovered {total_resources} resources across {len(network.get('vpcs', []))} VPCs")
    print(f"  Risk indicators: {len(risk_indicators.get('overprivileged_roles', []))} overprivileged roles, "
          f"{risk_indicators.get('long_lived_access_keys', 0)} long-lived keys, "
          f"{risk_indicators.get('public_endpoints_count', 0)} public endpoints")


if __name__ == "__main__":
    main()
