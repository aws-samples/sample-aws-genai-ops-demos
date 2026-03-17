"""
Account Resource Discovery for AWS Services Lifecycle Tracker

This module discovers actual AWS resources in the customer's account
and checks them against known deprecation schedules. This provides
personalized, relevant deprecation alerts based on what the customer
is actually using.
"""
import boto3
import os
from datetime import datetime
from typing import Dict, List, Any

# Get region from environment
REGION = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or 'us-east-1'

# Known deprecation schedules for AWS services
LAMBDA_RUNTIME_INFO = {
    "python3.7": {"deprecation": "2023-11-27", "end_of_support": "2024-11-27", "status": "end_of_life"},
    "python3.8": {"deprecation": "2024-10-14", "end_of_support": "2025-10-14", "status": "deprecated"},
    "python3.9": {"deprecation": "2025-10-01", "end_of_support": "2026-10-01", "status": "deprecated"},
    "python3.10": {"deprecation": "2026-10-01", "end_of_support": "2027-10-01", "status": "supported"},
    "python3.11": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "python3.12": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "python3.13": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "nodejs14.x": {"deprecation": "2023-11-27", "end_of_support": "2024-11-27", "status": "end_of_life"},
    "nodejs16.x": {"deprecation": "2024-03-11", "end_of_support": "2025-03-11", "status": "end_of_life"},
    "nodejs18.x": {"deprecation": "2025-04-30", "end_of_support": "2026-04-30", "status": "deprecated"},
    "nodejs20.x": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "nodejs22.x": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "java8": {"deprecation": "2023-12-31", "end_of_support": "2024-12-31", "status": "end_of_life"},
    "java8.al2": {"deprecation": "2024-01-08", "end_of_support": "2025-01-08", "status": "end_of_life"},
    "java11": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "java17": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "java21": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "dotnetcore3.1": {"deprecation": "2023-04-03", "end_of_support": "2024-04-03", "status": "end_of_life"},
    "dotnet6": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "dotnet8": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "go1.x": {"deprecation": "2023-12-31", "end_of_support": "2024-12-31", "status": "end_of_life"},
    "ruby2.7": {"deprecation": "2023-12-07", "end_of_support": "2024-12-07", "status": "end_of_life"},
    "ruby3.2": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "ruby3.3": {"deprecation": None, "end_of_support": None, "status": "supported"},
}

RDS_ENGINE_INFO = {
    "mysql-5.7": {"deprecation": "2023-10-01", "end_of_support": "2024-02-29", "status": "end_of_life"},
    "mysql-8.0": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "postgres-11": {"deprecation": "2023-11-09", "end_of_support": "2024-02-29", "status": "end_of_life"},
    "postgres-12": {"deprecation": "2024-11-14", "end_of_support": "2025-02-28", "status": "deprecated"},
    "postgres-13": {"deprecation": "2025-11-13", "end_of_support": "2026-02-28", "status": "deprecated"},
    "postgres-14": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "postgres-15": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "postgres-16": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "mariadb-10.3": {"deprecation": "2023-10-23", "end_of_support": "2024-10-23", "status": "end_of_life"},
    "mariadb-10.4": {"deprecation": "2024-06-18", "end_of_support": "2025-06-18", "status": "deprecated"},
    "mariadb-10.5": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "mariadb-10.6": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "aurora-mysql-5.7": {"deprecation": "2024-10-31", "end_of_support": "2024-10-31", "status": "end_of_life"},
    "aurora-mysql-8.0": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "aurora-postgresql-11": {"deprecation": "2024-02-29", "end_of_support": "2024-02-29", "status": "end_of_life"},
    "aurora-postgresql-12": {"deprecation": "2025-02-28", "end_of_support": "2025-02-28", "status": "deprecated"},
    "aurora-postgresql-13": {"deprecation": None, "end_of_support": None, "status": "supported"},
}

EKS_VERSION_INFO = {
    "1.23": {"deprecation": "2023-10-11", "end_of_support": "2024-10-11", "status": "end_of_life"},
    "1.24": {"deprecation": "2024-01-31", "end_of_support": "2025-01-31", "status": "end_of_life"},
    "1.25": {"deprecation": "2024-05-01", "end_of_support": "2025-05-01", "status": "deprecated"},
    "1.26": {"deprecation": "2024-06-11", "end_of_support": "2025-06-11", "status": "deprecated"},
    "1.27": {"deprecation": "2024-07-24", "end_of_support": "2025-07-24", "status": "deprecated"},
    "1.28": {"deprecation": "2025-01-01", "end_of_support": "2026-01-01", "status": "deprecated"},
    "1.29": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "1.30": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "1.31": {"deprecation": None, "end_of_support": None, "status": "supported"},
}

ELASTICACHE_ENGINE_INFO = {
    "redis-6": {"deprecation": "2024-10-01", "end_of_support": "2025-10-01", "status": "deprecated"},
    "redis-7": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "memcached-1.5": {"deprecation": "2024-05-01", "end_of_support": "2025-05-01", "status": "deprecated"},
    "memcached-1.6": {"deprecation": None, "end_of_support": None, "status": "supported"},
}

OPENSEARCH_VERSION_INFO = {
    "OpenSearch_1.0": {"deprecation": "2024-07-01", "end_of_support": "2025-07-01", "status": "deprecated"},
    "OpenSearch_1.1": {"deprecation": "2024-07-01", "end_of_support": "2025-07-01", "status": "deprecated"},
    "OpenSearch_1.2": {"deprecation": "2024-07-01", "end_of_support": "2025-07-01", "status": "deprecated"},
    "OpenSearch_1.3": {"deprecation": "2024-07-01", "end_of_support": "2025-07-01", "status": "deprecated"},
    "OpenSearch_2.3": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "OpenSearch_2.5": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "OpenSearch_2.7": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "OpenSearch_2.9": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "OpenSearch_2.11": {"deprecation": None, "end_of_support": None, "status": "supported"},
}

# MSK (Kafka) version info
MSK_VERSION_INFO = {
    "2.2.1": {"deprecation": "2023-06-01", "end_of_support": "2024-06-01", "status": "end_of_life"},
    "2.3.1": {"deprecation": "2023-09-01", "end_of_support": "2024-09-01", "status": "end_of_life"},
    "2.4.1": {"deprecation": "2024-01-01", "end_of_support": "2025-01-01", "status": "end_of_life"},
    "2.6.0": {"deprecation": "2024-06-01", "end_of_support": "2025-06-01", "status": "deprecated"},
    "2.7.0": {"deprecation": "2024-09-01", "end_of_support": "2025-09-01", "status": "deprecated"},
    "2.8.1": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "3.3.1": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "3.4.0": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "3.5.1": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "3.6.0": {"deprecation": None, "end_of_support": None, "status": "supported"},
}

# DocumentDB version info
DOCUMENTDB_VERSION_INFO = {
    "3.6": {"deprecation": "2024-01-01", "end_of_support": "2024-09-01", "status": "end_of_life"},
    "4.0": {"deprecation": "2025-04-01", "end_of_support": "2025-10-01", "status": "deprecated"},
    "5.0": {"deprecation": None, "end_of_support": None, "status": "supported"},
}

# Neptune engine version info
NEPTUNE_VERSION_INFO = {
    "1.0.1.0": {"deprecation": "2023-06-01", "end_of_support": "2024-06-01", "status": "end_of_life"},
    "1.0.2.0": {"deprecation": "2023-09-01", "end_of_support": "2024-09-01", "status": "end_of_life"},
    "1.0.3.0": {"deprecation": "2024-01-01", "end_of_support": "2025-01-01", "status": "end_of_life"},
    "1.0.4.0": {"deprecation": "2024-06-01", "end_of_support": "2025-06-01", "status": "deprecated"},
    "1.0.5.0": {"deprecation": "2024-12-01", "end_of_support": "2025-12-01", "status": "deprecated"},
    "1.1.0.0": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "1.2.0.0": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "1.3.0.0": {"deprecation": None, "end_of_support": None, "status": "supported"},
}

# Glue version info (Python/Spark)
GLUE_VERSION_INFO = {
    "glue-1.0": {"deprecation": "2023-06-01", "end_of_support": "2024-06-01", "status": "end_of_life"},
    "glue-2.0": {"deprecation": "2024-06-01", "end_of_support": "2025-06-01", "status": "deprecated"},
    "glue-3.0": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "glue-4.0": {"deprecation": None, "end_of_support": None, "status": "supported"},
}

# Elastic Beanstalk platform info (simplified - major platforms)
BEANSTALK_PLATFORM_INFO = {
    "python-3.7": {"deprecation": "2023-06-01", "end_of_support": "2024-06-01", "status": "end_of_life"},
    "python-3.8": {"deprecation": "2024-10-01", "end_of_support": "2025-10-01", "status": "deprecated"},
    "python-3.9": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "python-3.11": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "python-3.12": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "nodejs-14": {"deprecation": "2023-11-01", "end_of_support": "2024-11-01", "status": "end_of_life"},
    "nodejs-16": {"deprecation": "2024-03-01", "end_of_support": "2025-03-01", "status": "end_of_life"},
    "nodejs-18": {"deprecation": "2025-04-01", "end_of_support": "2026-04-01", "status": "deprecated"},
    "nodejs-20": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "java-8": {"deprecation": "2024-01-01", "end_of_support": "2025-01-01", "status": "end_of_life"},
    "java-11": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "java-17": {"deprecation": None, "end_of_support": None, "status": "supported"},
    "java-21": {"deprecation": None, "end_of_support": None, "status": "supported"},
}

# EC2 instance types approaching EOL (older generations)
EC2_INSTANCE_INFO = {
    "t1": {"deprecation": "2022-01-01", "end_of_support": "2023-01-01", "status": "end_of_life"},
    "m1": {"deprecation": "2022-01-01", "end_of_support": "2023-01-01", "status": "end_of_life"},
    "m2": {"deprecation": "2022-01-01", "end_of_support": "2023-01-01", "status": "end_of_life"},
    "c1": {"deprecation": "2022-01-01", "end_of_support": "2023-01-01", "status": "end_of_life"},
    "t2": {"deprecation": "2025-01-01", "end_of_support": "2026-01-01", "status": "deprecated"},
    "m3": {"deprecation": "2024-01-01", "end_of_support": "2025-01-01", "status": "end_of_life"},
    "m4": {"deprecation": "2025-06-01", "end_of_support": "2026-06-01", "status": "deprecated"},
    "c3": {"deprecation": "2024-01-01", "end_of_support": "2025-01-01", "status": "end_of_life"},
    "c4": {"deprecation": "2025-06-01", "end_of_support": "2026-06-01", "status": "deprecated"},
    "r3": {"deprecation": "2024-01-01", "end_of_support": "2025-01-01", "status": "end_of_life"},
    "r4": {"deprecation": "2025-06-01", "end_of_support": "2026-06-01", "status": "deprecated"},
}


def discover_lambda_functions(region: str = None) -> List[Dict]:
    """Discover Lambda functions and their runtimes in the account"""
    region = region or REGION
    lambda_client = boto3.client("lambda", region_name=region)
    items = []
    runtime_functions = {}
    
    try:
        paginator = lambda_client.get_paginator("list_functions")
        for page in paginator.paginate():
            for func in page["Functions"]:
                runtime = func.get("Runtime", "unknown")
                if runtime not in runtime_functions:
                    runtime_functions[runtime] = []
                runtime_functions[runtime].append(func["FunctionName"])
        
        for runtime, functions in runtime_functions.items():
            info = LAMBDA_RUNTIME_INFO.get(runtime, {"status": "unknown", "deprecation": None, "end_of_support": None})
            items.append({
                "service_name": "AWS Lambda",
                "item_id": f"lambda-{runtime.replace('.', '')}",
                "status": info["status"],
                "source_url": "https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html",
                "extraction_date": datetime.now().strftime("%Y-%m-%d"),
                "last_verified": datetime.now().isoformat() + "Z",
                "service_specific": {
                    "name": f"Lambda {runtime} Runtime",
                    "identifier": runtime,
                    "deprecation_date": info.get("deprecation") or "N/A",
                    "end_of_support_date": info.get("end_of_support") or "N/A",
                    "affected_resources": ", ".join(functions[:5]) + (f" (+{len(functions)-5} more)" if len(functions) > 5 else ""),
                    "total_affected": len(functions),
                }
            })
    except Exception as e:
        print(f"Error discovering Lambda functions: {e}")
    
    return items


def discover_rds_instances(region: str = None) -> List[Dict]:
    """Discover RDS instances and their engine versions"""
    region = region or REGION
    rds_client = boto3.client("rds", region_name=region)
    items = []
    engine_instances = {}
    
    try:
        paginator = rds_client.get_paginator("describe_db_instances")
        for page in paginator.paginate():
            for db in page["DBInstances"]:
                engine = db["Engine"]
                version = db["EngineVersion"]
                major = version.split('.')[0]
                key = f"{engine}-{major}"
                
                if key not in engine_instances:
                    engine_instances[key] = []
                engine_instances[key].append(db["DBInstanceIdentifier"])
        
        for engine_key, instances in engine_instances.items():
            info = RDS_ENGINE_INFO.get(engine_key, {"status": "unknown", "deprecation": None, "end_of_support": None})
            items.append({
                "service_name": "Amazon RDS",
                "item_id": f"rds-{engine_key}",
                "status": info["status"],
                "source_url": "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/",
                "extraction_date": datetime.now().strftime("%Y-%m-%d"),
                "last_verified": datetime.now().isoformat() + "Z",
                "service_specific": {
                    "name": f"RDS {engine_key.replace('-', ' ').title()}",
                    "identifier": engine_key,
                    "deprecation_date": info.get("deprecation") or "N/A",
                    "end_of_support_date": info.get("end_of_support") or "N/A",
                    "affected_resources": ", ".join(instances),
                    "total_affected": len(instances),
                }
            })
    except Exception as e:
        print(f"Error discovering RDS instances: {e}")
    
    return items


def discover_eks_clusters(region: str = None) -> List[Dict]:
    """Discover EKS clusters and their Kubernetes versions"""
    region = region or REGION
    eks_client = boto3.client("eks", region_name=region)
    items = []
    version_clusters = {}
    
    try:
        clusters = eks_client.list_clusters()["clusters"]
        for cluster_name in clusters:
            cluster = eks_client.describe_cluster(name=cluster_name)["cluster"]
            version = cluster["version"]
            if version not in version_clusters:
                version_clusters[version] = []
            version_clusters[version].append(cluster_name)
        
        for version, cluster_names in version_clusters.items():
            info = EKS_VERSION_INFO.get(version, {"status": "unknown", "deprecation": None, "end_of_support": None})
            items.append({
                "service_name": "Amazon EKS",
                "item_id": f"eks-{version.replace('.', '')}",
                "status": info["status"],
                "source_url": "https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html",
                "extraction_date": datetime.now().strftime("%Y-%m-%d"),
                "last_verified": datetime.now().isoformat() + "Z",
                "service_specific": {
                    "name": f"Kubernetes {version}",
                    "identifier": f"k8s-{version}",
                    "deprecation_date": info.get("deprecation") or "N/A",
                    "end_of_support_date": info.get("end_of_support") or "N/A",
                    "affected_resources": ", ".join(cluster_names),
                    "total_affected": len(cluster_names),
                }
            })
    except Exception as e:
        print(f"Error discovering EKS clusters: {e}")
    
    return items


def discover_elasticache_clusters(region: str = None) -> List[Dict]:
    """Discover ElastiCache clusters and their engine versions"""
    region = region or REGION
    elasticache_client = boto3.client("elasticache", region_name=region)
    items = []
    engine_clusters = {}
    
    try:
        paginator = elasticache_client.get_paginator("describe_cache_clusters")
        for page in paginator.paginate():
            for cluster in page["CacheClusters"]:
                engine = cluster["Engine"]
                version = cluster["EngineVersion"].split('.')[0]
                key = f"{engine}-{version}"
                
                if key not in engine_clusters:
                    engine_clusters[key] = []
                engine_clusters[key].append(cluster["CacheClusterId"])
        
        for engine_key, clusters in engine_clusters.items():
            info = ELASTICACHE_ENGINE_INFO.get(engine_key, {"status": "unknown", "deprecation": None, "end_of_support": None})
            items.append({
                "service_name": "Amazon ElastiCache",
                "item_id": f"elasticache-{engine_key}",
                "status": info["status"],
                "source_url": "https://docs.aws.amazon.com/AmazonElastiCache/latest/red-ug/",
                "extraction_date": datetime.now().strftime("%Y-%m-%d"),
                "last_verified": datetime.now().isoformat() + "Z",
                "service_specific": {
                    "name": f"ElastiCache {engine_key.replace('-', ' ').title()}",
                    "identifier": engine_key,
                    "deprecation_date": info.get("deprecation") or "N/A",
                    "end_of_support_date": info.get("end_of_support") or "N/A",
                    "affected_resources": ", ".join(clusters),
                    "total_affected": len(clusters),
                }
            })
    except Exception as e:
        print(f"Error discovering ElastiCache clusters: {e}")
    
    return items


def discover_opensearch_domains(region: str = None) -> List[Dict]:
    """Discover OpenSearch domains and their versions"""
    region = region or REGION
    opensearch_client = boto3.client("opensearch", region_name=region)
    items = []
    version_domains = {}
    
    try:
        domains = opensearch_client.list_domain_names()["DomainNames"]
        for domain_info in domains:
            domain_name = domain_info["DomainName"]
            domain = opensearch_client.describe_domain(DomainName=domain_name)["DomainStatus"]
            version = domain.get("EngineVersion", "unknown")
            
            if version not in version_domains:
                version_domains[version] = []
            version_domains[version].append(domain_name)
        
        for version, domain_names in version_domains.items():
            info = OPENSEARCH_VERSION_INFO.get(version, {"status": "unknown", "deprecation": None, "end_of_support": None})
            items.append({
                "service_name": "Amazon OpenSearch",
                "item_id": f"opensearch-{version.replace('.', '').replace('_', '')}",
                "status": info["status"],
                "source_url": "https://docs.aws.amazon.com/opensearch-service/latest/developerguide/",
                "extraction_date": datetime.now().strftime("%Y-%m-%d"),
                "last_verified": datetime.now().isoformat() + "Z",
                "service_specific": {
                    "name": f"OpenSearch {version}",
                    "identifier": version,
                    "deprecation_date": info.get("deprecation") or "N/A",
                    "end_of_support_date": info.get("end_of_support") or "N/A",
                    "affected_resources": ", ".join(domain_names),
                    "total_affected": len(domain_names),
                }
            })
    except Exception as e:
        print(f"Error discovering OpenSearch domains: {e}")
    
    return items


def discover_msk_clusters(region: str = None) -> List[Dict]:
    """Discover MSK (Kafka) clusters and their versions"""
    region = region or REGION
    msk_client = boto3.client("kafka", region_name=region)
    items = []
    version_clusters = {}
    
    try:
        paginator = msk_client.get_paginator("list_clusters_v2")
        for page in paginator.paginate():
            for cluster in page.get("ClusterInfoList", []):
                cluster_name = cluster.get("ClusterName", "unknown")
                # Get Kafka version from provisioned or serverless config
                provisioned = cluster.get("Provisioned", {})
                kafka_version = provisioned.get("CurrentBrokerSoftwareInfo", {}).get("KafkaVersion", "unknown")
                
                if kafka_version not in version_clusters:
                    version_clusters[kafka_version] = []
                version_clusters[kafka_version].append(cluster_name)
        
        for version, cluster_names in version_clusters.items():
            info = MSK_VERSION_INFO.get(version, {"status": "unknown", "deprecation": None, "end_of_support": None})
            items.append({
                "service_name": "Amazon MSK",
                "item_id": f"msk-{version.replace('.', '')}",
                "status": info["status"],
                "source_url": "https://docs.aws.amazon.com/msk/latest/developerguide/supported-kafka-versions.html",
                "extraction_date": datetime.now().strftime("%Y-%m-%d"),
                "last_verified": datetime.now().isoformat() + "Z",
                "service_specific": {
                    "name": f"Apache Kafka {version}",
                    "identifier": f"kafka-{version}",
                    "deprecation_date": info.get("deprecation") or "N/A",
                    "end_of_support_date": info.get("end_of_support") or "N/A",
                    "affected_resources": ", ".join(cluster_names),
                    "total_affected": len(cluster_names),
                }
            })
    except Exception as e:
        print(f"Error discovering MSK clusters: {e}")
    
    return items


def discover_documentdb_clusters(region: str = None) -> List[Dict]:
    """Discover DocumentDB clusters and their engine versions"""
    region = region or REGION
    docdb_client = boto3.client("docdb", region_name=region)
    items = []
    version_clusters = {}
    
    try:
        paginator = docdb_client.get_paginator("describe_db_clusters")
        for page in paginator.paginate():
            for cluster in page.get("DBClusters", []):
                if cluster.get("Engine") == "docdb":
                    cluster_id = cluster.get("DBClusterIdentifier", "unknown")
                    version = cluster.get("EngineVersion", "unknown").split('.')[0] + "." + cluster.get("EngineVersion", "unknown").split('.')[1] if '.' in cluster.get("EngineVersion", "") else cluster.get("EngineVersion", "unknown")
                    
                    if version not in version_clusters:
                        version_clusters[version] = []
                    version_clusters[version].append(cluster_id)
        
        for version, cluster_names in version_clusters.items():
            info = DOCUMENTDB_VERSION_INFO.get(version, {"status": "unknown", "deprecation": None, "end_of_support": None})
            items.append({
                "service_name": "Amazon DocumentDB",
                "item_id": f"docdb-{version.replace('.', '')}",
                "status": info["status"],
                "source_url": "https://docs.aws.amazon.com/documentdb/latest/developerguide/",
                "extraction_date": datetime.now().strftime("%Y-%m-%d"),
                "last_verified": datetime.now().isoformat() + "Z",
                "service_specific": {
                    "name": f"DocumentDB {version} (MongoDB compatibility)",
                    "identifier": f"docdb-{version}",
                    "deprecation_date": info.get("deprecation") or "N/A",
                    "end_of_support_date": info.get("end_of_support") or "N/A",
                    "affected_resources": ", ".join(cluster_names),
                    "total_affected": len(cluster_names),
                }
            })
    except Exception as e:
        print(f"Error discovering DocumentDB clusters: {e}")
    
    return items


def discover_neptune_clusters(region: str = None) -> List[Dict]:
    """Discover Neptune clusters and their engine versions"""
    region = region or REGION
    neptune_client = boto3.client("neptune", region_name=region)
    items = []
    version_clusters = {}
    
    try:
        paginator = neptune_client.get_paginator("describe_db_clusters")
        for page in paginator.paginate():
            for cluster in page.get("DBClusters", []):
                if cluster.get("Engine") == "neptune":
                    cluster_id = cluster.get("DBClusterIdentifier", "unknown")
                    version = cluster.get("EngineVersion", "unknown")
                    
                    if version not in version_clusters:
                        version_clusters[version] = []
                    version_clusters[version].append(cluster_id)
        
        for version, cluster_names in version_clusters.items():
            info = NEPTUNE_VERSION_INFO.get(version, {"status": "unknown", "deprecation": None, "end_of_support": None})
            items.append({
                "service_name": "Amazon Neptune",
                "item_id": f"neptune-{version.replace('.', '')}",
                "status": info["status"],
                "source_url": "https://docs.aws.amazon.com/neptune/latest/userguide/",
                "extraction_date": datetime.now().strftime("%Y-%m-%d"),
                "last_verified": datetime.now().isoformat() + "Z",
                "service_specific": {
                    "name": f"Neptune {version}",
                    "identifier": f"neptune-{version}",
                    "deprecation_date": info.get("deprecation") or "N/A",
                    "end_of_support_date": info.get("end_of_support") or "N/A",
                    "affected_resources": ", ".join(cluster_names),
                    "total_affected": len(cluster_names),
                }
            })
    except Exception as e:
        print(f"Error discovering Neptune clusters: {e}")
    
    return items


def discover_glue_jobs(region: str = None) -> List[Dict]:
    """Discover Glue jobs and their versions"""
    region = region or REGION
    glue_client = boto3.client("glue", region_name=region)
    items = []
    version_jobs = {}
    
    try:
        paginator = glue_client.get_paginator("get_jobs")
        for page in paginator.paginate():
            for job in page.get("Jobs", []):
                job_name = job.get("Name", "unknown")
                glue_version = job.get("GlueVersion", "unknown")
                key = f"glue-{glue_version}"
                
                if key not in version_jobs:
                    version_jobs[key] = []
                version_jobs[key].append(job_name)
        
        for version_key, job_names in version_jobs.items():
            info = GLUE_VERSION_INFO.get(version_key, {"status": "unknown", "deprecation": None, "end_of_support": None})
            items.append({
                "service_name": "AWS Glue",
                "item_id": version_key.replace('.', ''),
                "status": info["status"],
                "source_url": "https://docs.aws.amazon.com/glue/latest/dg/release-notes.html",
                "extraction_date": datetime.now().strftime("%Y-%m-%d"),
                "last_verified": datetime.now().isoformat() + "Z",
                "service_specific": {
                    "name": f"Glue {version_key.replace('glue-', '')}",
                    "identifier": version_key,
                    "deprecation_date": info.get("deprecation") or "N/A",
                    "end_of_support_date": info.get("end_of_support") or "N/A",
                    "affected_resources": ", ".join(job_names[:5]) + (f" (+{len(job_names)-5} more)" if len(job_names) > 5 else ""),
                    "total_affected": len(job_names),
                }
            })
    except Exception as e:
        print(f"Error discovering Glue jobs: {e}")
    
    return items


def discover_beanstalk_environments(region: str = None) -> List[Dict]:
    """Discover Elastic Beanstalk environments and their platform versions"""
    region = region or REGION
    eb_client = boto3.client("elasticbeanstalk", region_name=region)
    items = []
    platform_envs = {}
    
    try:
        envs = eb_client.describe_environments().get("Environments", [])
        for env in envs:
            env_name = env.get("EnvironmentName", "unknown")
            platform = env.get("PlatformArn", "")
            
            # Extract platform info (e.g., python-3.8, nodejs-18)
            platform_key = "unknown"
            if "python" in platform.lower():
                for ver in ["3.7", "3.8", "3.9", "3.11", "3.12"]:
                    if ver in platform:
                        platform_key = f"python-{ver}"
                        break
            elif "node" in platform.lower():
                for ver in ["14", "16", "18", "20"]:
                    if f"node.js {ver}" in platform.lower() or f"nodejs-{ver}" in platform.lower():
                        platform_key = f"nodejs-{ver}"
                        break
            elif "java" in platform.lower() or "corretto" in platform.lower():
                for ver in ["8", "11", "17", "21"]:
                    if f"corretto {ver}" in platform.lower() or f"java-{ver}" in platform.lower():
                        platform_key = f"java-{ver}"
                        break
            
            if platform_key not in platform_envs:
                platform_envs[platform_key] = []
            platform_envs[platform_key].append(env_name)
        
        for platform_key, env_names in platform_envs.items():
            info = BEANSTALK_PLATFORM_INFO.get(platform_key, {"status": "unknown", "deprecation": None, "end_of_support": None})
            items.append({
                "service_name": "AWS Elastic Beanstalk",
                "item_id": f"beanstalk-{platform_key.replace('.', '').replace('-', '')}",
                "status": info["status"],
                "source_url": "https://docs.aws.amazon.com/elasticbeanstalk/latest/platforms/",
                "extraction_date": datetime.now().strftime("%Y-%m-%d"),
                "last_verified": datetime.now().isoformat() + "Z",
                "service_specific": {
                    "name": f"Beanstalk {platform_key}",
                    "identifier": platform_key,
                    "deprecation_date": info.get("deprecation") or "N/A",
                    "end_of_support_date": info.get("end_of_support") or "N/A",
                    "affected_resources": ", ".join(env_names),
                    "total_affected": len(env_names),
                }
            })
    except Exception as e:
        print(f"Error discovering Elastic Beanstalk environments: {e}")
    
    return items


def discover_ec2_instances(region: str = None) -> List[Dict]:
    """Discover EC2 instances with older instance types"""
    region = region or REGION
    ec2_client = boto3.client("ec2", region_name=region)
    items = []
    type_instances = {}
    
    try:
        paginator = ec2_client.get_paginator("describe_instances")
        for page in paginator.paginate(Filters=[{"Name": "instance-state-name", "Values": ["running", "stopped"]}]):
            for reservation in page.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    instance_id = instance.get("InstanceId", "unknown")
                    instance_type = instance.get("InstanceType", "unknown")
                    
                    # Extract instance family (e.g., t2, m4, c5)
                    family = instance_type.split('.')[0] if '.' in instance_type else instance_type
                    
                    if family not in type_instances:
                        type_instances[family] = []
                    type_instances[family].append(instance_id)
        
        for family, instance_ids in type_instances.items():
            info = EC2_INSTANCE_INFO.get(family, {"status": "supported", "deprecation": None, "end_of_support": None})
            # Only report deprecated/EOL instance families
            if info["status"] in ["deprecated", "end_of_life"]:
                items.append({
                    "service_name": "Amazon EC2",
                    "item_id": f"ec2-{family}",
                    "status": info["status"],
                    "source_url": "https://aws.amazon.com/ec2/previous-generation/",
                    "extraction_date": datetime.now().strftime("%Y-%m-%d"),
                    "last_verified": datetime.now().isoformat() + "Z",
                    "service_specific": {
                        "name": f"EC2 {family.upper()} Instance Family",
                        "identifier": family,
                        "deprecation_date": info.get("deprecation") or "N/A",
                        "end_of_support_date": info.get("end_of_support") or "N/A",
                        "affected_resources": ", ".join(instance_ids[:5]) + (f" (+{len(instance_ids)-5} more)" if len(instance_ids) > 5 else ""),
                        "total_affected": len(instance_ids),
                    }
                })
    except Exception as e:
        print(f"Error discovering EC2 instances: {e}")
    
    return items


def save_to_dynamodb(items: List[Dict], table_name: str = "aws-services-lifecycle", region: str = None) -> Dict:
    """
    Save discovered items to DynamoDB, replacing existing data.
    
    Args:
        items: List of deprecation items to save
        table_name: DynamoDB table name
        region: AWS region
    
    Returns:
        Dictionary with save results
    """
    region = region or REGION
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)
    
    try:
        # Clear existing items first
        scan = table.scan()
        with table.batch_writer() as batch:
            for item in scan.get("Items", []):
                batch.delete_item(Key={
                    "service_name": item["service_name"],
                    "item_id": item["item_id"]
                })
        
        # Write new items
        with table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)
        
        return {
            "success": True,
            "items_saved": len(items),
            "table_name": table_name
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def discover_all_resources(region: str = None, include_supported: bool = True) -> Dict:
    """
    Discover all AWS resources in the account and check for deprecations.
    
    Scans 11 AWS services:
    - Lambda (runtimes)
    - RDS (engine versions)
    - EKS (Kubernetes versions)
    - ElastiCache (Redis/Memcached versions)
    - OpenSearch (engine versions)
    - MSK (Kafka versions)
    - DocumentDB (MongoDB compatibility versions)
    - Neptune (graph DB versions)
    - Glue (ETL job versions)
    - Elastic Beanstalk (platform versions)
    - EC2 (older instance families)
    
    Args:
        region: AWS region to scan (defaults to environment variable)
        include_supported: If True, include all resources. If False, only deprecated ones.
    
    Returns:
        Dictionary with discovery results and summary
    """
    region = region or REGION
    all_items = []
    services_scanned = []
    services_failed = []
    
    # Discover resources from each service
    print(f"Discovering resources in {region}...")
    
    # Core services (most common)
    try:
        lambda_items = discover_lambda_functions(region)
        all_items.extend(lambda_items)
        services_scanned.append("Lambda")
    except Exception as e:
        services_failed.append(f"Lambda: {e}")
    
    try:
        rds_items = discover_rds_instances(region)
        all_items.extend(rds_items)
        services_scanned.append("RDS")
    except Exception as e:
        services_failed.append(f"RDS: {e}")
    
    try:
        eks_items = discover_eks_clusters(region)
        all_items.extend(eks_items)
        services_scanned.append("EKS")
    except Exception as e:
        services_failed.append(f"EKS: {e}")
    
    try:
        elasticache_items = discover_elasticache_clusters(region)
        all_items.extend(elasticache_items)
        services_scanned.append("ElastiCache")
    except Exception as e:
        services_failed.append(f"ElastiCache: {e}")
    
    try:
        opensearch_items = discover_opensearch_domains(region)
        all_items.extend(opensearch_items)
        services_scanned.append("OpenSearch")
    except Exception as e:
        services_failed.append(f"OpenSearch: {e}")
    
    # Additional services
    try:
        msk_items = discover_msk_clusters(region)
        all_items.extend(msk_items)
        services_scanned.append("MSK")
    except Exception as e:
        services_failed.append(f"MSK: {e}")
    
    try:
        docdb_items = discover_documentdb_clusters(region)
        all_items.extend(docdb_items)
        services_scanned.append("DocumentDB")
    except Exception as e:
        services_failed.append(f"DocumentDB: {e}")
    
    try:
        neptune_items = discover_neptune_clusters(region)
        all_items.extend(neptune_items)
        services_scanned.append("Neptune")
    except Exception as e:
        services_failed.append(f"Neptune: {e}")
    
    try:
        glue_items = discover_glue_jobs(region)
        all_items.extend(glue_items)
        services_scanned.append("Glue")
    except Exception as e:
        services_failed.append(f"Glue: {e}")
    
    try:
        beanstalk_items = discover_beanstalk_environments(region)
        all_items.extend(beanstalk_items)
        services_scanned.append("Elastic Beanstalk")
    except Exception as e:
        services_failed.append(f"Elastic Beanstalk: {e}")
    
    try:
        ec2_items = discover_ec2_instances(region)
        all_items.extend(ec2_items)
        services_scanned.append("EC2")
    except Exception as e:
        services_failed.append(f"EC2: {e}")
    
    # Filter if needed
    if not include_supported:
        all_items = [i for i in all_items if i["status"] in ["deprecated", "end_of_life"]]
    
    # Calculate summary
    deprecated_count = len([i for i in all_items if i["status"] == "deprecated"])
    eol_count = len([i for i in all_items if i["status"] == "end_of_life"])
    supported_count = len([i for i in all_items if i["status"] == "supported"])
    
    return {
        "success": True,
        "region": region,
        "items": all_items,
        "summary": {
            "total": len(all_items),
            "end_of_life": eol_count,
            "deprecated": deprecated_count,
            "supported": supported_count,
            "needs_attention": deprecated_count + eol_count,
        },
        "services_scanned": services_scanned,
        "services_failed": services_failed,
        "discovery_date": datetime.now().isoformat() + "Z"
    }


def discover_and_save(region: str = None, include_supported: bool = True, table_name: str = "aws-services-lifecycle") -> Dict:
    """
    Discover all resources and save to DynamoDB in one operation.
    This is the main entry point for the agent integration.
    
    Args:
        region: AWS region to scan
        include_supported: Include supported resources (not just deprecated)
        table_name: DynamoDB table name
    
    Returns:
        Dictionary with discovery and save results
    """
    # Discover resources
    discovery_result = discover_all_resources(region, include_supported)
    
    if not discovery_result["success"]:
        return discovery_result
    
    # Save to DynamoDB
    save_result = save_to_dynamodb(discovery_result["items"], table_name, region)
    
    if not save_result["success"]:
        return {
            "success": False,
            "error": f"Discovery succeeded but save failed: {save_result['error']}",
            "discovery_result": discovery_result
        }
    
    return {
        "success": True,
        "region": discovery_result["region"],
        "items_discovered": len(discovery_result["items"]),
        "items_saved": save_result["items_saved"],
        "summary": discovery_result["summary"],
        "discovery_date": discovery_result["discovery_date"]
    }
