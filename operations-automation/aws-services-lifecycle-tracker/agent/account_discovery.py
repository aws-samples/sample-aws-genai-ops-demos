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

# ---------------------------------------------------------------------------
# Lifecycle verdicts come from the extraction pipeline's DynamoDB rows.
#
# This module previously carried ~10 hand-written *_INFO dicts with hardcoded
# deprecation dates and statuses (issue #99 I1/I2). Those went stale silently
# (single commit, never maintained) while the extraction pipeline kept fresh,
# correct data one table away. Discovery now only detects WHAT is running in
# the account; WHETHER it is deprecated is answered by joining the extraction
# rows. No match means status 'unknown' - never a stale hardcoded verdict.
# ---------------------------------------------------------------------------

import re

# The one detection heuristic kept: which EC2 instance families count as
# "previous generation" (per https://aws.amazon.com/ec2/previous-generation/).
# This is membership knowledge (which families to flag), not lifecycle data -
# families never leave this list. Dates/verdicts still come from the lifecycle
# table when an 'ec2' extraction source exists; otherwise the row is reported
# as deprecated with no dates.
EC2_PREVIOUS_GENERATION_FAMILIES = {
    "t1", "m1", "m2", "c1", "m3", "c3", "r3", "m4", "c4", "r4", "t2", "i2",
}

# RDS API engine names -> extraction 'engine' vocabulary
_RDS_ENGINE_ALIASES = {
    "postgres": "postgresql",
    "docdb": "documentdb",
}

# SQL Server internal major version -> product year (immutable mapping facts)
_SQLSERVER_VERSION_YEARS = {
    "11": "2012", "12": "2014", "13": "2016",
    "14": "2017", "15": "2019", "16": "2022", "17": "2025",
}


def _normalize(value) -> str:
    """Normalize identifiers for matching: lowercase, dashes, alnum/dot only."""
    if not value:
        return ""
    text = str(value).lower().replace("_", "-").replace(" ", "-")
    return re.sub(r"[^a-z0-9.\-]", "", text)


class LifecycleIndex:
    """Per-service lookup of extraction-owned lifecycle rows (issue #99 I1).

    Loads the lifecycle table rows for a service on first use (skipping
    provenance-tagged inventory rows, including discovery's own) and indexes
    them by normalized identifier/version/name. lookup() tries exact matches
    first, then prefix containment either way (longest indexed key wins).
    """

    def __init__(self, table_name: str = None, region: str = None):
        table_name = table_name or os.environ.get("LIFECYCLE_TABLE_NAME", "aws-services-lifecycle")
        dynamodb = boto3.resource("dynamodb", region_name=region or REGION)
        self._table = dynamodb.Table(table_name)
        self._cache: Dict[str, Dict[str, Dict]] = {}

    def _load(self, service_key: str) -> Dict[str, Dict]:
        rows = []
        kwargs = {
            "KeyConditionExpression": "service_name = :s",
            "ExpressionAttributeValues": {":s": service_key},
        }
        try:
            response = self._table.query(**kwargs)
            rows.extend(response.get("Items", []))
            while "LastEvaluatedKey" in response:
                response = self._table.query(ExclusiveStartKey=response["LastEvaluatedKey"], **kwargs)
                rows.extend(response.get("Items", []))
        except Exception as e:
            print(f"Warning: could not load lifecycle rows for '{service_key}': {e}")
            return {}

        index: Dict[str, Dict] = {}
        for row in rows:
            if row.get("provenance"):
                continue  # inventory rows are not lifecycle knowledge
            specific = row.get("service_specific", {}) or {}
            entry = {
                "status": row.get("status", "unknown"),
                "deprecation_date": str(specific.get("deprecation_date") or "") or "N/A",
                "end_of_support_date": str(
                    specific.get("end_of_support_date")
                    or specific.get("end_of_standard_support_date")
                    or specific.get("eol_date")
                    or specific.get("end_of_life_date")
                    or ""
                ) or "N/A",
                "item_id": row.get("item_id", ""),
            }
            for candidate in (specific.get("identifier"), specific.get("version"), specific.get("name")):
                key = _normalize(candidate)
                if key and key not in index:
                    index[key] = entry
        return index

    def lookup(self, service_key: str, candidates: List) -> Dict:
        """Best lifecycle match for any candidate identifier, or None."""
        if service_key not in self._cache:
            self._cache[service_key] = self._load(service_key)
        index = self._cache[service_key]

        normalized = [_normalize(c) for c in candidates if c]
        for cand in normalized:
            if cand in index:
                return index[cand]

        # Prefix fallback, restricted to VERSION boundaries: the shorter side
        # must end where a version segment begins ('.', '-', or a digit->letter
        # edge). Without this, Lambda's 'nodejs' row (Node.js 0.10) would claim
        # every nodejs2x.x runtime as deprecated.
        def _boundary_prefix(short: str, long: str) -> bool:
            if not long.startswith(short) or len(long) == len(short):
                return False
            nxt = long[len(short)]
            prev = short[-1]
            return nxt in ".-" or (prev.isdigit() and not nxt.isdigit()) or (prev in ".-")

        best, best_len = None, 0
        for cand in normalized:
            if len(cand) < 3:
                continue
            for key, entry in index.items():
                if len(key) < 3:
                    continue
                if (_boundary_prefix(cand, key) or _boundary_prefix(key, cand)) and len(key) > best_len:
                    best, best_len = entry, len(key)
        return best


def build_inventory_item(service_key: str, identifier: str, display_name: str,
                         candidates: List, affected_resources: str, total_affected: int,
                         source_url: str, index: "LifecycleIndex",
                         fallback_status: str = "unknown") -> Dict:
    """Emit a unified inventory row keyed like extraction rows (#98 E7).

    service_name uses the extraction config key (e.g. 'lambda', not
    'AWS Lambda') and item_id is prefixed 'inventory#', so inventory shares
    the key vocabulary of the rest of the system (UI filters, Health
    enrichment) while staying distinguishable and provenance-tagged.
    """
    match = index.lookup(service_key, candidates)
    now = datetime.now()
    return {
        "service_name": service_key,
        "item_id": f"inventory#{identifier}",
        "status": match["status"] if match else fallback_status,
        "source_url": source_url,
        "extraction_date": now.strftime("%Y-%m-%d"),
        "last_verified": now.isoformat() + "Z",
        "service_specific": {
            "name": display_name,
            "identifier": identifier,
            "deprecation_date": (match or {}).get("deprecation_date", "N/A"),
            "end_of_support_date": (match or {}).get("end_of_support_date", "N/A"),
            "affected_resources": affected_resources,
            "total_affected": total_affected,
            "matched_lifecycle_item": (match or {}).get("item_id", ""),
        },
    }


def discover_lambda_functions(region: str = None, index: LifecycleIndex = None) -> List[Dict]:
    """Discover Lambda functions and their runtimes in the account"""
    region = region or REGION
    index = index or LifecycleIndex(region=region)
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
            items.append(build_inventory_item(
                service_key="lambda",
                identifier=runtime,
                display_name=f"Lambda {runtime} Runtime",
                candidates=[runtime],
                affected_resources=", ".join(functions[:5]) + (f" (+{len(functions)-5} more)" if len(functions) > 5 else ""),
                total_affected=len(functions),
                source_url="https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html",
                index=index,
            ))
    except Exception as e:
        print(f"Error discovering Lambda functions: {e}")
    
    return items


def _rds_match_candidates(engine: str, version: str) -> List[str]:
    """Candidate identifiers to match an RDS engine/version against
    extraction rows (which use slugs like 'mysql-8.0.35', 'postgresql-17.6',
    'oracle-19c', 'sqlserver-2019')."""
    base = engine.split("-")[0] if engine.startswith(("oracle", "sqlserver")) else engine
    base = _RDS_ENGINE_ALIASES.get(base, base)
    # Aurora MySQL reports '8.0.mysql_aurora.3.11.1' / '5.7.mysql_aurora.2.12.6':
    # the part after 'mysql_aurora.' is the Aurora version the release
    # calendar (and therefore the facts) are keyed on ('aurora-mysql-3.11').
    # Without this the candidates were 'aurora-mysql-8.0' / 'aurora-mysql-8',
    # which never match and can prefix-match the unrelated 'aurora-mysql-8.4'.
    if "mysql_aurora." in version:
        version = version.split("mysql_aurora.", 1)[1]
    parts = version.split(".")
    candidates = [f"{base}-{version}"]
    if len(parts) >= 2:
        candidates.append(f"{base}-{parts[0]}.{parts[1]}")
    candidates.append(f"{base}-{parts[0]}")
    if base == "sqlserver":
        year = _SQLSERVER_VERSION_YEARS.get(parts[0])
        if year:
            candidates.insert(0, f"sqlserver-{year}")
    if base == "oracle":
        candidates.append(f"oracle-{parts[0]}c")
    return candidates


def discover_rds_instances(region: str = None, index: LifecycleIndex = None) -> List[Dict]:
    """Discover RDS instances and their engine versions"""
    region = region or REGION
    index = index or LifecycleIndex(region=region)
    rds_client = boto3.client("rds", region_name=region)
    items = []
    engine_instances = {}
    
    try:
        paginator = rds_client.get_paginator("describe_db_instances")
        for page in paginator.paginate():
            for db in page["DBInstances"]:
                engine = db["Engine"]
                # DocumentDB and Neptune instances are returned by the RDS API too;
                # their own scanners report them at cluster level with the right
                # facts, so skip them here (they produced 'unknown' duplicates).
                if engine in ("docdb", "neptune"):
                    continue
                version = db["EngineVersion"]
                if "mysql_aurora." in version:
                    # '8.0.mysql_aurora.3.11.1' -> '3.11.1' (see _rds_match_candidates)
                    version = version.split("mysql_aurora.", 1)[1]
                # Group by the version that carries the lifecycle: major.minor for
                # Aurora/RDS engines ('aurora-mysql-3.11', 'postgres-14'), so two
                # Aurora MySQL 3.x versions are not lumped together as '3'.
                parts = version.split('.')
                major = ".".join(parts[:2]) if engine.startswith("aurora-mysql") and len(parts) >= 2 else parts[0]
                key = (engine, major, version)
                
                if key not in engine_instances:
                    engine_instances[key] = []
                engine_instances[key].append(db["DBInstanceIdentifier"])
        
        for (engine, major, version), instances in engine_instances.items():
            # Aurora engines have their own extraction source/config key
            service_key = "aurora" if engine.startswith("aurora") else "rds"
            engine_key = f"{engine}-{major}"
            items.append(build_inventory_item(
                service_key=service_key,
                identifier=engine_key,
                display_name=f"RDS {engine_key.replace('-', ' ').title()}",
                candidates=_rds_match_candidates(engine, version),
                affected_resources=", ".join(instances),
                total_affected=len(instances),
                source_url="https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/",
                index=index,
            ))
    except Exception as e:
        print(f"Error discovering RDS instances: {e}")
    
    return items


def discover_eks_clusters(region: str = None, index: LifecycleIndex = None) -> List[Dict]:
    """Discover EKS clusters and their Kubernetes versions"""
    region = region or REGION
    index = index or LifecycleIndex(region=region)
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
            items.append(build_inventory_item(
                service_key="eks",
                identifier=f"k8s-{version}",
                display_name=f"Kubernetes {version}",
                candidates=[version, f"k8s-{version}", f"eks-{version}"],
                affected_resources=", ".join(cluster_names),
                total_affected=len(cluster_names),
                source_url="https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html",
                index=index,
            ))
    except Exception as e:
        print(f"Error discovering EKS clusters: {e}")
    
    return items


def discover_elasticache_clusters(region: str = None, index: LifecycleIndex = None) -> List[Dict]:
    """Discover ElastiCache clusters and their engine versions"""
    region = region or REGION
    index = index or LifecycleIndex(region=region)
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
            items.append(build_inventory_item(
                service_key="elasticache",
                identifier=engine_key,
                display_name=f"ElastiCache {engine_key.replace('-', ' ').title()}",
                candidates=[engine_key],
                affected_resources=", ".join(clusters),
                total_affected=len(clusters),
                source_url="https://docs.aws.amazon.com/AmazonElastiCache/latest/red-ug/",
                index=index,
            ))
    except Exception as e:
        print(f"Error discovering ElastiCache clusters: {e}")
    
    return items


def discover_opensearch_domains(region: str = None, index: LifecycleIndex = None) -> List[Dict]:
    """Discover OpenSearch domains and their versions"""
    region = region or REGION
    index = index or LifecycleIndex(region=region)
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
            bare_version = version.split("_")[-1] if "_" in version else version
            items.append(build_inventory_item(
                service_key="opensearch",
                identifier=version,
                display_name=f"OpenSearch {version}",
                candidates=[version, bare_version, f"opensearch-{bare_version}"],
                affected_resources=", ".join(domain_names),
                total_affected=len(domain_names),
                source_url="https://docs.aws.amazon.com/opensearch-service/latest/developerguide/",
                index=index,
            ))
    except Exception as e:
        print(f"Error discovering OpenSearch domains: {e}")
    
    return items


def discover_msk_clusters(region: str = None, index: LifecycleIndex = None) -> List[Dict]:
    """Discover MSK (Kafka) clusters and their versions"""
    region = region or REGION
    index = index or LifecycleIndex(region=region)
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
            items.append(build_inventory_item(
                service_key="msk",
                identifier=f"kafka-{version}",
                display_name=f"Apache Kafka {version}",
                candidates=[f"kafka-{version}", version],
                affected_resources=", ".join(cluster_names),
                total_affected=len(cluster_names),
                source_url="https://docs.aws.amazon.com/msk/latest/developerguide/supported-kafka-versions.html",
                index=index,
            ))
    except Exception as e:
        print(f"Error discovering MSK clusters: {e}")
    
    return items


def discover_documentdb_clusters(region: str = None, index: LifecycleIndex = None) -> List[Dict]:
    """Discover DocumentDB clusters and their engine versions"""
    region = region or REGION
    index = index or LifecycleIndex(region=region)
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
            items.append(build_inventory_item(
                service_key="documentdb",
                identifier=f"docdb-{version}",
                display_name=f"DocumentDB {version} (MongoDB compatibility)",
                candidates=[f"docdb-{version}", f"documentdb-{version}", version],
                affected_resources=", ".join(cluster_names),
                total_affected=len(cluster_names),
                source_url="https://docs.aws.amazon.com/documentdb/latest/developerguide/",
                index=index,
            ))
    except Exception as e:
        print(f"Error discovering DocumentDB clusters: {e}")
    
    return items


def discover_neptune_clusters(region: str = None, index: LifecycleIndex = None) -> List[Dict]:
    """Discover Neptune clusters and their engine versions"""
    region = region or REGION
    index = index or LifecycleIndex(region=region)
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
            items.append(build_inventory_item(
                service_key="neptune",
                identifier=f"neptune-{version}",
                display_name=f"Neptune {version}",
                candidates=[f"neptune-{version}", version],
                affected_resources=", ".join(cluster_names),
                total_affected=len(cluster_names),
                source_url="https://docs.aws.amazon.com/neptune/latest/userguide/",
                index=index,
            ))
    except Exception as e:
        print(f"Error discovering Neptune clusters: {e}")
    
    return items


def discover_glue_jobs(region: str = None, index: LifecycleIndex = None) -> List[Dict]:
    """Discover Glue jobs and their versions"""
    region = region or REGION
    index = index or LifecycleIndex(region=region)
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
            items.append(build_inventory_item(
                service_key="glue",
                identifier=version_key,
                display_name=f"Glue {version_key.replace('glue-', '')}",
                candidates=[version_key, version_key.replace('glue-', '')],
                affected_resources=", ".join(job_names[:5]) + (f" (+{len(job_names)-5} more)" if len(job_names) > 5 else ""),
                total_affected=len(job_names),
                source_url="https://docs.aws.amazon.com/glue/latest/dg/release-notes.html",
                index=index,
            ))
    except Exception as e:
        print(f"Error discovering Glue jobs: {e}")
    
    return items


def discover_beanstalk_environments(region: str = None, index: LifecycleIndex = None) -> List[Dict]:
    """Discover Elastic Beanstalk environments and their platform versions"""
    region = region or REGION
    index = index or LifecycleIndex(region=region)
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
            items.append(build_inventory_item(
                service_key="elasticbeanstalk",
                identifier=platform_key,
                display_name=f"Beanstalk {platform_key}",
                candidates=[platform_key, platform_key.replace('-', ' ')],
                affected_resources=", ".join(env_names),
                total_affected=len(env_names),
                source_url="https://docs.aws.amazon.com/elasticbeanstalk/latest/platforms/",
                index=index,
            ))
    except Exception as e:
        print(f"Error discovering Elastic Beanstalk environments: {e}")
    
    return items


def discover_ec2_instances(region: str = None, index: LifecycleIndex = None) -> List[Dict]:
    """Discover EC2 instances with previous-generation instance types.

    EC2 has no extraction source today, so the verdict falls back to
    'deprecated' for families on the previous-generation list (membership in
    that list IS the deprecation signal); dates come from the lifecycle table
    if an 'ec2' extraction source is ever configured.
    """
    region = region or REGION
    index = index or LifecycleIndex(region=region)
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
            # Only report previous-generation instance families
            if family not in EC2_PREVIOUS_GENERATION_FAMILIES:
                continue
            items.append(build_inventory_item(
                service_key="ec2",
                identifier=family,
                display_name=f"EC2 {family.upper()} Instance Family",
                candidates=[family, f"ec2-{family}"],
                affected_resources=", ".join(instance_ids[:5]) + (f" (+{len(instance_ids)-5} more)" if len(instance_ids) > 5 else ""),
                total_affected=len(instance_ids),
                source_url="https://aws.amazon.com/ec2/previous-generation/",
                index=index,
                fallback_status="deprecated",
            ))
    except Exception as e:
        print(f"Error discovering EC2 instances: {e}")
    
    return items


# Provenance tag identifying rows written by account discovery (issue #116).
# Kept on every inventory row for traceability; will carry account/region
# provenance dimensions when the multi-account roadmap (#99 I4) lands.
DISCOVERY_PROVENANCE = "account_discovery"

# Discovered assets live in their own table, fully decoupled from the public
# deprecation facts in aws-services-lifecycle (issue #116 follow-on).
INVENTORY_TABLE_NAME = os.environ.get("INVENTORY_TABLE_NAME", "aws-account-inventory")

# Scanner display label -> config service keys its inventory rows use.
# The RDS scanner emits both rds and aurora rows (aurora engines route to the
# aurora service key), so a successful RDS scan owns both reconciliation scopes.
SCANNER_SERVICE_KEYS = {
    "Lambda": ["lambda"],
    "RDS": ["rds", "aurora"],
    "EKS": ["eks"],
    "ElastiCache": ["elasticache"],
    "OpenSearch": ["opensearch"],
    "MSK": ["msk"],
    "DocumentDB": ["documentdb"],
    "Neptune": ["neptune"],
    "Glue": ["glue"],
    "Elastic Beanstalk": ["elasticbeanstalk"],
    "EC2": ["ec2"],
}


def save_to_dynamodb(items: List[Dict], table_name: str = None, region: str = None,
                     run_id: str = None, scanned_services: List[str] = None) -> Dict:
    """
    Upsert discovered inventory rows and reconcile stale ones (issue #116).

    Writes go to the dedicated aws-account-inventory table - never to the
    extraction facts table. Reconciliation is run-id based and scoped:
    1. every item is tagged with this run's discovery_run_id and provenance,
    2. items are upserted by key (idempotent re-runs),
    3. stale rows (older run_id, i.e. resources no longer seen) are deleted
       ONLY within the services this run actually scanned successfully.
       A scanner that failed leaves its service's inventory untouched
       instead of having it wiped as "stale".

    Args:
        items: Discovered inventory items to save
        table_name: DynamoDB table override (defaults to INVENTORY_TABLE_NAME)
        region: AWS region
        run_id: Unique id for this discovery run (generated if omitted)
        scanned_services: Service keys whose scanners completed successfully;
            reconciliation is confined to these. Defaults to the service keys
            present in items (which loses empty-result scopes - pass it).

    Returns:
        Dictionary with save results
    """
    import uuid

    region = region or REGION
    table_name = table_name or INVENTORY_TABLE_NAME

    # Hard guard (issue #116): reconciliation below deletes rows outside the
    # current run, so pointing this at the extraction facts table would destroy
    # deprecation data. A caller passing the lifecycle table is always a bug -
    # refuse rather than corrupt.
    lifecycle_table_name = os.environ.get("LIFECYCLE_TABLE_NAME", "aws-services-lifecycle")
    if table_name == lifecycle_table_name:
        return {
            "success": False,
            "error": (
                f"Refusing to write inventory to the extraction facts table "
                f"'{table_name}'. Inventory belongs in '{INVENTORY_TABLE_NAME}' (issue #116)."
            ),
        }

    run_id = run_id or str(uuid.uuid4())
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    try:
        for item in items:
            item["provenance"] = DISCOVERY_PROVENANCE
            item["discovery_run_id"] = run_id

        # Upsert this run's inventory (put on an existing key replaces it)
        with table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)

        # Reconcile per successfully-scanned service: query that service's
        # inventory rows and delete those not written by this run.
        if scanned_services is None:
            scanned_services = sorted({i["service_name"] for i in items})

        stale_keys = []
        for service_key in scanned_services:
            kwargs = {
                "KeyConditionExpression": "service_name = :s",
                "ExpressionAttributeValues": {":s": service_key},
                "ProjectionExpression": "service_name, item_id, discovery_run_id",
            }
            response = table.query(**kwargs)
            while True:
                for row in response.get("Items", []):
                    if row.get("discovery_run_id") != run_id:
                        stale_keys.append({
                            "service_name": row["service_name"],
                            "item_id": row["item_id"],
                        })
                if "LastEvaluatedKey" not in response:
                    break
                response = table.query(ExclusiveStartKey=response["LastEvaluatedKey"], **kwargs)

        with table.batch_writer() as batch:
            for key in stale_keys:
                batch.delete_item(Key=key)

        return {
            "success": True,
            "items_saved": len(items),
            "stale_removed": len(stale_keys),
            "run_id": run_id,
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
    
    # One shared lifecycle index for the whole run: each service's extraction
    # rows are loaded once and reused across discover_* calls (issue #99 I1).
    index = LifecycleIndex(region=region)
    
    # Discover resources from each service
    print(f"Discovering resources in {region}...")
    
    # Core services (most common)
    try:
        lambda_items = discover_lambda_functions(region, index)
        all_items.extend(lambda_items)
        services_scanned.append("Lambda")
    except Exception as e:
        services_failed.append(f"Lambda: {e}")
    
    try:
        rds_items = discover_rds_instances(region, index)
        all_items.extend(rds_items)
        services_scanned.append("RDS")
    except Exception as e:
        services_failed.append(f"RDS: {e}")
    
    try:
        eks_items = discover_eks_clusters(region, index)
        all_items.extend(eks_items)
        services_scanned.append("EKS")
    except Exception as e:
        services_failed.append(f"EKS: {e}")
    
    try:
        elasticache_items = discover_elasticache_clusters(region, index)
        all_items.extend(elasticache_items)
        services_scanned.append("ElastiCache")
    except Exception as e:
        services_failed.append(f"ElastiCache: {e}")
    
    try:
        opensearch_items = discover_opensearch_domains(region, index)
        all_items.extend(opensearch_items)
        services_scanned.append("OpenSearch")
    except Exception as e:
        services_failed.append(f"OpenSearch: {e}")
    
    # Additional services
    try:
        msk_items = discover_msk_clusters(region, index)
        all_items.extend(msk_items)
        services_scanned.append("MSK")
    except Exception as e:
        services_failed.append(f"MSK: {e}")
    
    try:
        docdb_items = discover_documentdb_clusters(region, index)
        all_items.extend(docdb_items)
        services_scanned.append("DocumentDB")
    except Exception as e:
        services_failed.append(f"DocumentDB: {e}")
    
    try:
        neptune_items = discover_neptune_clusters(region, index)
        all_items.extend(neptune_items)
        services_scanned.append("Neptune")
    except Exception as e:
        services_failed.append(f"Neptune: {e}")
    
    try:
        glue_items = discover_glue_jobs(region, index)
        all_items.extend(glue_items)
        services_scanned.append("Glue")
    except Exception as e:
        services_failed.append(f"Glue: {e}")
    
    try:
        beanstalk_items = discover_beanstalk_environments(region, index)
        all_items.extend(beanstalk_items)
        services_scanned.append("Elastic Beanstalk")
    except Exception as e:
        services_failed.append(f"Elastic Beanstalk: {e}")
    
    try:
        ec2_items = discover_ec2_instances(region, index)
        all_items.extend(ec2_items)
        services_scanned.append("EC2")
    except Exception as e:
        services_failed.append(f"EC2: {e}")
    
    # Filter if needed
    if not include_supported:
        all_items = [i for i in all_items if i["status"] in ["deprecated", "end_of_life"]]
    
    # Service keys covered by the scanners that completed successfully -
    # reconciliation must be confined to these (issue #116): a failed scanner
    # keeps its previous inventory instead of having it wiped as stale.
    scanned_service_keys = sorted({
        key
        for label in services_scanned
        for key in SCANNER_SERVICE_KEYS.get(label, [])
    })
    
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
        "scanned_service_keys": scanned_service_keys,
        "services_failed": services_failed,
        "discovery_date": datetime.now().isoformat() + "Z"
    }


def discover_and_save(region: str = None, include_supported: bool = True, table_name: str = None) -> Dict:
    """
    Discover all resources and save to the inventory table in one operation.
    This is the main entry point for the agent integration.
    
    Args:
        region: AWS region to scan
        include_supported: Include supported resources (not just deprecated)
        table_name: Inventory table override (defaults to INVENTORY_TABLE_NAME)
    
    Returns:
        Dictionary with discovery and save results
    """
    # Discover resources
    discovery_result = discover_all_resources(region, include_supported)
    
    if not discovery_result["success"]:
        return discovery_result
    
    # Save to the inventory table; reconciliation is confined to the scopes
    # whose scanners succeeded (issue #116).
    save_result = save_to_dynamodb(
        discovery_result["items"], table_name, region,
        scanned_services=discovery_result["scanned_service_keys"],
    )
    
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
        "stale_removed": save_result.get("stale_removed", 0),
        "summary": discovery_result["summary"],
        "services_failed": discovery_result["services_failed"],
        "discovery_date": discovery_result["discovery_date"]
    }
