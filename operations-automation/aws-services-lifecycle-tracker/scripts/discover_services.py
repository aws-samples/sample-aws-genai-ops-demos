#!/usr/bin/env python3
"""
Script de découverte des services AWS avec pages de dépréciation.
Exécution manuelle : python scripts/discover_services.py

Parcourt les patterns connus de documentation AWS pour identifier
les services publiant des informations de cycle de vie.
"""
import json
import argparse
from typing import Optional


# Known AWS documentation URL patterns for lifecycle/deprecation pages
# Each entry: (service_name, url, lifecycle_type, category)
KNOWN_LIFECYCLE_PATTERNS: list[dict] = [
    # --- runtime_versions ---
    {
        "service_name": "lambda",
        "url": "https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html",
        "lifecycle_type": "runtime_versions",
        "description": "Lambda runtime deprecation schedule",
    },
    {
        "service_name": "elasticbeanstalk",
        "url": "https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/platforms-schedule.html",
        "lifecycle_type": "runtime_versions",
        "description": "Elastic Beanstalk platform retirement schedule",
    },
    {
        "service_name": "cloudfront",
        "url": "https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/lambda-edge-runtime.html",
        "lifecycle_type": "runtime_versions",
        "description": "Lambda@Edge and CloudFront Functions runtime versions",
    },
    {
        "service_name": "apprunner",
        "url": "https://docs.aws.amazon.com/apprunner/latest/dg/service-source-code.html",
        "lifecycle_type": "runtime_versions",
        "description": "App Runner managed runtime versions",
    },
    # --- engine_versions ---
    {
        "service_name": "rds",
        "url": "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/MySQL.Concepts.VersionMgmt.html",
        "lifecycle_type": "engine_versions",
        "description": "RDS MySQL engine version lifecycle",
    },
    {
        "service_name": "rds_postgresql",
        "url": "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_PostgreSQL.html",
        "lifecycle_type": "engine_versions",
        "description": "RDS PostgreSQL engine version lifecycle",
    },
    {
        "service_name": "aurora",
        "url": "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.VersionPolicy.html",
        "lifecycle_type": "engine_versions",
        "description": "Aurora engine version lifecycle",
    },
    {
        "service_name": "elasticache",
        "url": "https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/extended-support-versions.html",
        "lifecycle_type": "engine_versions",
        "description": "ElastiCache Redis OSS version lifecycle",
    },
    {
        "service_name": "memorydb",
        "url": "https://docs.aws.amazon.com/memorydb/latest/devguide/supported-engine-versions.html",
        "lifecycle_type": "engine_versions",
        "description": "MemoryDB Redis engine version lifecycle",
    },
    {
        "service_name": "opensearch",
        "url": "https://docs.aws.amazon.com/opensearch-service/latest/developerguide/what-is.html",
        "lifecycle_type": "engine_versions",
        "description": "OpenSearch engine version lifecycle",
    },
    {
        "service_name": "documentdb",
        "url": "https://docs.aws.amazon.com/documentdb/latest/developerguide/release-notes.html",
        "lifecycle_type": "engine_versions",
        "description": "DocumentDB engine version lifecycle",
    },
    {
        "service_name": "neptune",
        "url": "https://docs.aws.amazon.com/neptune/latest/userguide/engine-releases.html",
        "lifecycle_type": "engine_versions",
        "description": "Neptune engine version lifecycle",
    },
    {
        "service_name": "msk",
        "url": "https://docs.aws.amazon.com/msk/latest/developerguide/supported-kafka-versions.html",
        "lifecycle_type": "engine_versions",
        "description": "MSK Apache Kafka version lifecycle",
    },
    {
        "service_name": "redshift",
        "url": "https://docs.aws.amazon.com/redshift/latest/mgmt/cluster-versions.html",
        "lifecycle_type": "engine_versions",
        "description": "Redshift cluster version lifecycle",
    },
    {
        "service_name": "athena",
        "url": "https://docs.aws.amazon.com/athena/latest/ug/engine-versions-reference.html",
        "lifecycle_type": "engine_versions",
        "description": "Athena engine version lifecycle",
    },
    {
        "service_name": "glue",
        "url": "https://docs.aws.amazon.com/glue/latest/dg/glue-version-support-policy.html",
        "lifecycle_type": "engine_versions",
        "description": "AWS Glue version support policy",
    },
    {
        "service_name": "emr",
        "url": "https://docs.aws.amazon.com/emr/latest/ReleaseGuide/emr-release-components.html",
        "lifecycle_type": "engine_versions",
        "description": "EMR release version lifecycle",
    },
    {
        "service_name": "keyspaces",
        "url": "https://docs.aws.amazon.com/keyspaces/latest/devguide/programmatic.endpoints.html",
        "lifecycle_type": "engine_versions",
        "description": "Amazon Keyspaces (Cassandra) version lifecycle",
    },
    # --- platform_versions ---
    {
        "service_name": "eks",
        "url": "https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html",
        "lifecycle_type": "platform_versions",
        "description": "EKS Kubernetes version lifecycle",
    },
    {
        "service_name": "ecs",
        "url": "https://docs.aws.amazon.com/AmazonECS/latest/developerguide/platform-versions.html",
        "lifecycle_type": "platform_versions",
        "description": "ECS Fargate platform version lifecycle",
    },
    {
        "service_name": "batch",
        "url": "https://docs.aws.amazon.com/batch/latest/userguide/platform-versions.html",
        "lifecycle_type": "platform_versions",
        "description": "AWS Batch platform version lifecycle",
    },
    {
        "service_name": "appmesh",
        "url": "https://docs.aws.amazon.com/app-mesh/latest/userguide/envoy-releases.html",
        "lifecycle_type": "platform_versions",
        "description": "App Mesh Envoy proxy version lifecycle",
    },
    {
        "service_name": "amplify",
        "url": "https://docs.aws.amazon.com/amplify/latest/userguide/build-settings.html",
        "lifecycle_type": "platform_versions",
        "description": "AWS Amplify build image versions",
    },
    # --- ml_models ---
    {
        "service_name": "bedrock",
        "url": "https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle.html",
        "lifecycle_type": "ml_models",
        "description": "Bedrock model lifecycle (legacy and EOL models)",
    },
    {
        "service_name": "sagemaker",
        "url": "https://docs.aws.amazon.com/sagemaker/latest/dg/supported-instance-types.html",
        "lifecycle_type": "ml_models",
        "description": "SageMaker framework version lifecycle",
    },
    {
        "service_name": "comprehend",
        "url": "https://docs.aws.amazon.com/comprehend/latest/dg/functionality-versions.html",
        "lifecycle_type": "ml_models",
        "description": "Comprehend model version lifecycle",
    },
    {
        "service_name": "rekognition",
        "url": "https://docs.aws.amazon.com/rekognition/latest/dg/face-detection-model.html",
        "lifecycle_type": "ml_models",
        "description": "Rekognition model version lifecycle",
    },
    {
        "service_name": "lex",
        "url": "https://docs.aws.amazon.com/lexv2/latest/dg/migration.html",
        "lifecycle_type": "ml_models",
        "description": "Amazon Lex V1 to V2 migration",
    },
    {
        "service_name": "personalize",
        "url": "https://docs.aws.amazon.com/personalize/latest/dg/native-recipe-new-item-USER_PERSONALIZATION.html",
        "lifecycle_type": "ml_models",
        "description": "Amazon Personalize recipe versions",
    },
    # --- protocol_versions ---
    {
        "service_name": "apigateway",
        "url": "https://docs.aws.amazon.com/apigateway/latest/developerguide/api-ref.html",
        "lifecycle_type": "protocol_versions",
        "description": "API Gateway version and TLS protocol lifecycle",
    },
    {
        "service_name": "iot",
        "url": "https://docs.aws.amazon.com/iot/latest/developerguide/protocols.html",
        "lifecycle_type": "protocol_versions",
        "description": "AWS IoT Core protocol version lifecycle",
    },
    {
        "service_name": "transfer",
        "url": "https://docs.aws.amazon.com/transfer/latest/userguide/security-policies.html",
        "lifecycle_type": "protocol_versions",
        "description": "AWS Transfer Family security/protocol policies",
    },
    {
        "service_name": "elasticloadbalancing",
        "url": "https://docs.aws.amazon.com/elasticloadbalancing/latest/application/create-https-listener.html",
        "lifecycle_type": "protocol_versions",
        "description": "ELB TLS/SSL policy deprecation",
    },
    {
        "service_name": "directconnect",
        "url": "https://docs.aws.amazon.com/directconnect/latest/UserGuide/encryption-in-transit.html",
        "lifecycle_type": "protocol_versions",
        "description": "Direct Connect MACsec and encryption protocol versions",
    },
]

# Mapping lifecycle_type -> category description
CATEGORY_DESCRIPTIONS = {
    "runtime_versions": "Services with runtime/language version deprecation schedules",
    "engine_versions": "Services with database/processing engine version lifecycle",
    "platform_versions": "Services with platform/container version lifecycle",
    "ml_models": "Services with machine learning model version lifecycle",
    "protocol_versions": "Services with protocol/API version deprecation",
}

# URL pattern keywords for categorization heuristics
# Higher-weight patterns (multi-word or very specific) are listed first
CATEGORIZATION_PATTERNS: dict[str, list[str]] = {
    "runtime_versions": [
        "lambda-runtimes", "lambda-edge-runtime", "platforms-schedule",
        "source-code", "runtime", "runtimes",
    ],
    "engine_versions": [
        "engine-versions", "supported-kafka", "cluster-versions",
        "glue-version", "version-support", "release-components",
        "extended-support", "release-notes", "engine",
    ],
    "platform_versions": [
        "kubernetes-versions", "platform-versions", "platform-version",
        "envoy-releases", "build-settings",
    ],
    "ml_models": [
        "model-lifecycle", "functionality-versions", "face-detection-model",
        "supported-instance", "migration", "recipe",
    ],
    "protocol_versions": [
        "security-policies", "encryption-in-transit", "https-listener",
        "protocol", "api-ref",
    ],
}


def discover_lifecycle_pages() -> list[dict]:
    """
    Découvre les pages de documentation contenant des informations lifecycle.

    Scans known AWS documentation URL patterns to identify services
    that publish lifecycle/deprecation information.

    Returns:
        Liste de candidats : {service_name, url, lifecycle_type, category, description}
        lifecycle_type: runtime_versions | engine_versions | platform_versions |
                       ml_models | protocol_versions
    """
    candidates = []
    for pattern in KNOWN_LIFECYCLE_PATTERNS:
        candidate = {
            "service_name": pattern["service_name"],
            "url": pattern["url"],
            "lifecycle_type": pattern["lifecycle_type"],
            "category": CATEGORY_DESCRIPTIONS.get(pattern["lifecycle_type"], "Unknown"),
            "description": pattern.get("description", ""),
        }
        candidates.append(candidate)
    return candidates


def categorize_service(service_name: str, url: str) -> str:
    """
    Catégorise un service par type de cycle de vie en analysant l'URL.

    Uses URL pattern matching to determine the lifecycle type category
    for a given service documentation URL. Longer (more specific) pattern
    matches are weighted higher to avoid ambiguity.

    Args:
        service_name: Name of the AWS service
        url: Documentation URL for the service

    Returns:
        One of: runtime_versions, engine_versions, platform_versions,
                ml_models, protocol_versions
    """
    url_lower = url.lower()

    # Check each category's patterns against the URL
    # Weight by pattern specificity (longer patterns = more specific = higher weight)
    scores: dict[str, float] = {cat: 0.0 for cat in CATEGORIZATION_PATTERNS}

    for category, keywords in CATEGORIZATION_PATTERNS.items():
        for keyword in keywords:
            if keyword in url_lower:
                # Longer/more specific patterns get higher weight
                scores[category] += len(keyword)

    # Return the category with the highest score
    best_category = max(scores, key=scores.get)  # type: ignore[arg-type]

    # If no pattern matched, use service name heuristics
    if scores[best_category] == 0:
        return _categorize_by_service_name(service_name)

    return best_category


def _categorize_by_service_name(service_name: str) -> str:
    """Fallback categorization using service name heuristics."""
    name_lower = service_name.lower()

    runtime_services = {"lambda", "elasticbeanstalk", "cloudfront", "apprunner"}
    engine_services = {
        "rds", "aurora", "elasticache", "memorydb", "opensearch",
        "documentdb", "neptune", "msk", "redshift", "athena",
        "glue", "emr", "keyspaces",
    }
    platform_services = {"eks", "ecs", "batch", "appmesh", "amplify"}
    ml_services = {
        "bedrock", "sagemaker", "comprehend", "rekognition", "lex", "personalize",
    }
    protocol_services = {
        "apigateway", "iot", "transfer", "elasticloadbalancing", "directconnect",
    }

    if name_lower in runtime_services:
        return "runtime_versions"
    elif name_lower in engine_services:
        return "engine_versions"
    elif name_lower in platform_services:
        return "platform_versions"
    elif name_lower in ml_services:
        return "ml_models"
    elif name_lower in protocol_services:
        return "protocol_versions"

    # Default fallback
    return "engine_versions"


def generate_config_template(candidates: list[dict]) -> dict:
    """
    Génère un template Service_Config pour les candidats découverts.

    Produces a JSON-compatible dict with Service_Config entries for each
    discovered candidate, ready to be merged into service_configs.json.

    Args:
        candidates: List of discovered service candidates from discover_lifecycle_pages()

    Returns:
        Dict with 'services' key containing Service_Config templates
    """
    services = {}

    for candidate in candidates:
        service_name = candidate["service_name"]
        lifecycle_type = candidate["lifecycle_type"]

        # Generate appropriate schema_key and item_properties based on lifecycle_type
        schema_key, item_properties, required_fields = _get_schema_for_type(lifecycle_type)

        config_entry = {
            "name": _format_service_display_name(service_name),
            "documentation_urls": [candidate["url"]],
            "extraction_focus": f"Extract lifecycle/deprecation information for {_format_service_display_name(service_name)}. "
                               f"Locate version tables or deprecation schedules. "
                               f"For each item, extract: name, identifier, relevant dates, and status.",
            "schema_key": schema_key,
            "item_properties": item_properties,
            "required_fields": required_fields,
            "enabled": True,
            "health_event_mapping": service_name.upper().replace("_", ""),
            "last_extraction": "",
            "extraction_count": 0,
        }
        services[service_name] = config_entry

    return {"services": services}


def _get_schema_for_type(lifecycle_type: str) -> tuple[str, dict, list[str]]:
    """Returns (schema_key, item_properties, required_fields) for a lifecycle type."""
    if lifecycle_type == "runtime_versions":
        return (
            "runtimes",
            {
                "name": "Runtime name",
                "identifier": "Runtime identifier",
                "version": "Runtime version",
                "deprecation_date": "Deprecation date",
                "end_of_support_date": "End of support date",
                "status": "Current status",
            },
            ["name", "identifier", "deprecation_date", "status"],
        )
    elif lifecycle_type == "engine_versions":
        return (
            "engine_versions",
            {
                "name": "Engine name and version",
                "identifier": "Engine version identifier",
                "engine": "Engine type",
                "version": "Version number",
                "end_of_standard_support_date": "End of standard support date",
                "end_of_extended_support_date": "End of extended support date",
                "status": "Current status",
            },
            ["name", "identifier", "engine", "version"],
        )
    elif lifecycle_type == "platform_versions":
        return (
            "platform_versions",
            {
                "name": "Platform version name",
                "identifier": "Platform version identifier",
                "platform": "Platform type",
                "version": "Version number",
                "deprecation_date": "Deprecation date",
                "end_of_support_date": "End of support date",
                "status": "Current status",
            },
            ["name", "identifier", "version", "status"],
        )
    elif lifecycle_type == "ml_models":
        return (
            "models",
            {
                "name": "Model name",
                "identifier": "Model identifier",
                "provider": "Model provider or framework",
                "version": "Model version",
                "deprecation_date": "Deprecation or legacy date",
                "end_of_life_date": "End of life date",
                "status": "Current status",
            },
            ["name", "identifier", "status"],
        )
    elif lifecycle_type == "protocol_versions":
        return (
            "protocols",
            {
                "name": "Protocol or API version name",
                "identifier": "Protocol version identifier",
                "protocol": "Protocol type (TLS, HTTP, MQTT, etc.)",
                "version": "Protocol version",
                "deprecation_date": "Deprecation date",
                "end_of_support_date": "End of support date",
                "status": "Current status",
            },
            ["name", "identifier", "status"],
        )
    else:
        # Fallback
        return (
            "versions",
            {
                "name": "Item name",
                "identifier": "Item identifier",
                "version": "Version",
                "deprecation_date": "Deprecation date",
                "status": "Current status",
            },
            ["name", "identifier", "status"],
        )


def _format_service_display_name(service_name: str) -> str:
    """Convert service_name to a human-readable display name."""
    name_map = {
        "lambda": "AWS Lambda",
        "eks": "Amazon EKS",
        "ecs": "Amazon ECS",
        "rds": "Amazon RDS",
        "rds_postgresql": "Amazon RDS (PostgreSQL)",
        "aurora": "Amazon Aurora",
        "elasticache": "Amazon ElastiCache",
        "memorydb": "Amazon MemoryDB",
        "opensearch": "Amazon OpenSearch Service",
        "documentdb": "Amazon DocumentDB",
        "neptune": "Amazon Neptune",
        "msk": "Amazon MSK",
        "redshift": "Amazon Redshift",
        "athena": "Amazon Athena",
        "glue": "AWS Glue",
        "emr": "Amazon EMR",
        "keyspaces": "Amazon Keyspaces",
        "elasticbeanstalk": "AWS Elastic Beanstalk",
        "cloudfront": "Amazon CloudFront",
        "apprunner": "AWS App Runner",
        "batch": "AWS Batch",
        "appmesh": "AWS App Mesh",
        "amplify": "AWS Amplify",
        "bedrock": "Amazon Bedrock",
        "sagemaker": "Amazon SageMaker",
        "comprehend": "Amazon Comprehend",
        "rekognition": "Amazon Rekognition",
        "lex": "Amazon Lex",
        "personalize": "Amazon Personalize",
        "apigateway": "Amazon API Gateway",
        "iot": "AWS IoT Core",
        "transfer": "AWS Transfer Family",
        "elasticloadbalancing": "Elastic Load Balancing",
        "directconnect": "AWS Direct Connect",
        "dynamodb": "Amazon DynamoDB",
    }
    return name_map.get(service_name, f"AWS {service_name.replace('_', ' ').title()}")


def print_summary(candidates: list[dict]) -> None:
    """Print a human-readable summary of discovered services."""
    print(f"\n{'='*70}")
    print(f"AWS Service Lifecycle Discovery Results")
    print(f"{'='*70}")
    print(f"\nTotal services discovered: {len(candidates)}")
    print()

    # Group by lifecycle_type
    by_type: dict[str, list[dict]] = {}
    for c in candidates:
        lt = c["lifecycle_type"]
        if lt not in by_type:
            by_type[lt] = []
        by_type[lt].append(c)

    for lifecycle_type, services in sorted(by_type.items()):
        desc = CATEGORY_DESCRIPTIONS.get(lifecycle_type, "")
        print(f"\n📂 {lifecycle_type} ({len(services)} services)")
        print(f"   {desc}")
        print(f"   {'─'*50}")
        for svc in services:
            print(f"   • {_format_service_display_name(svc['service_name'])}")
            print(f"     {svc['url']}")

    print(f"\n{'='*70}")


def main():
    parser = argparse.ArgumentParser(
        description="Discover AWS services with lifecycle/deprecation documentation pages"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output file path for generated config template (JSON)",
    )
    parser.add_argument(
        "--category", "-c",
        type=str,
        default=None,
        choices=list(CATEGORY_DESCRIPTIONS.keys()),
        help="Filter by lifecycle type category",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON to stdout",
    )
    args = parser.parse_args()

    # Discover services
    candidates = discover_lifecycle_pages()

    # Apply category filter if specified
    if args.category:
        candidates = [c for c in candidates if c["lifecycle_type"] == args.category]

    if args.json:
        # Output raw candidates as JSON
        print(json.dumps(candidates, indent=2))
    else:
        # Print human-readable summary
        print_summary(candidates)

    # Generate and optionally save config template
    if args.output:
        config_template = generate_config_template(candidates)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(config_template, f, indent=2, ensure_ascii=False)
        print(f"\n✅ Config template written to: {args.output}")
        print(f"   Contains {len(config_template['services'])} service configurations")


if __name__ == "__main__":
    main()
