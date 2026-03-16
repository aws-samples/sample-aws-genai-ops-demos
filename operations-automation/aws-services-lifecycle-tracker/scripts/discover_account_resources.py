#!/usr/bin/env python3
"""
Discover actual AWS resources in your account and check for deprecations.
This script scans your account and populates the lifecycle tracker with
only the resources YOU are actually using.
"""
import boto3
import json
from datetime import datetime
from typing import Dict, List, Any

# Known deprecation dates for AWS services (None means still supported)
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
}


class AccountResourceDiscovery:
    def __init__(self, region: str = "eu-west-1", include_supported: bool = True):
        self.region = region
        self.include_supported = include_supported  # Show all resources, not just deprecated
        self.lambda_client = boto3.client("lambda", region_name=region)
        self.rds_client = boto3.client("rds", region_name=region)
        self.eks_client = boto3.client("eks", region_name=region)
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.lifecycle_table = self.dynamodb.Table("aws-services-lifecycle")
        self.config_table = self.dynamodb.Table("service-extraction-config")
        
    def discover_lambda_functions(self) -> List[Dict]:
        """Discover Lambda functions and their runtimes"""
        items = []
        paginator = self.lambda_client.get_paginator("list_functions")
        
        runtime_functions = {}
        
        for page in paginator.paginate():
            for func in page["Functions"]:
                runtime = func.get("Runtime", "unknown")
                if runtime not in runtime_functions:
                    runtime_functions[runtime] = []
                runtime_functions[runtime].append(func["FunctionName"])
        
        for runtime, functions in runtime_functions.items():
            info = LAMBDA_RUNTIME_INFO.get(runtime, {"status": "unknown"})
            
            # Skip if not deprecated and we only want deprecated
            if not self.include_supported and info["status"] == "supported":
                continue
                
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
                    "affected_functions": ", ".join(functions[:5]),
                    "total_affected": len(functions),
                }
            })
        return items

    def discover_rds_instances(self) -> List[Dict]:
        """Discover RDS instances and their engine versions"""
        items = []
        try:
            paginator = self.rds_client.get_paginator("describe_db_instances")
            engine_instances = {}
            
            for page in paginator.paginate():
                for db in page["DBInstances"]:
                    engine = db["Engine"]
                    version = db["EngineVersion"]
                    major = version.split('.')[0]
                    key = f"{engine}-{major}"
                    
                    if key not in engine_instances:
                        engine_instances[key] = []
                    engine_instances[key].append({
                        "id": db["DBInstanceIdentifier"],
                        "version": version,
                    })
            
            for engine_key, instances in engine_instances.items():
                info = RDS_ENGINE_INFO.get(engine_key, {"status": "unknown"})
                
                if not self.include_supported and info["status"] == "supported":
                    continue
                    
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
                        "affected_instances": ", ".join([i["id"] for i in instances]),
                        "total_affected": len(instances),
                    }
                })
        except Exception as e:
            print(f"  Warning: Could not scan RDS: {e}")
        return items


    def discover_eks_clusters(self) -> List[Dict]:
        """Discover EKS clusters and their Kubernetes versions"""
        items = []
        try:
            clusters = self.eks_client.list_clusters()["clusters"]
            version_clusters = {}
            
            for cluster_name in clusters:
                cluster = self.eks_client.describe_cluster(name=cluster_name)["cluster"]
                version = cluster["version"]
                if version not in version_clusters:
                    version_clusters[version] = []
                version_clusters[version].append(cluster_name)
            
            for version, cluster_names in version_clusters.items():
                info = EKS_VERSION_INFO.get(version, {"status": "unknown"})
                
                if not self.include_supported and info["status"] == "supported":
                    continue
                    
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
                        "affected_clusters": ", ".join(cluster_names),
                        "total_affected": len(cluster_names),
                    }
                })
        except Exception as e:
            print(f"  Warning: Could not scan EKS: {e}")
        return items

    def save_to_dynamodb(self, items: List[Dict]):
        """Save discovered items to DynamoDB"""
        scan = self.lifecycle_table.scan()
        with self.lifecycle_table.batch_writer() as batch:
            for item in scan["Items"]:
                batch.delete_item(Key={"service_name": item["service_name"], "item_id": item["item_id"]})
        
        with self.lifecycle_table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)
        print(f"Saved {len(items)} items to DynamoDB")


    def run(self):
        """Run full discovery and populate DynamoDB"""
        print(f"Discovering resources in {self.region}...")
        all_items = []
        
        print("Scanning Lambda functions...")
        lambda_items = self.discover_lambda_functions()
        all_items.extend(lambda_items)
        print(f"  Found {len(lambda_items)} Lambda runtimes")
        
        print("Scanning RDS instances...")
        rds_items = self.discover_rds_instances()
        all_items.extend(rds_items)
        print(f"  Found {len(rds_items)} RDS engines")
        
        print("Scanning EKS clusters...")
        eks_items = self.discover_eks_clusters()
        all_items.extend(eks_items)
        print(f"  Found {len(eks_items)} EKS versions")
        
        if all_items:
            self.save_to_dynamodb(all_items)
        
        # Summary
        deprecated = [i for i in all_items if i["status"] in ["deprecated", "end_of_life"]]
        supported = [i for i in all_items if i["status"] == "supported"]
        
        print(f"\n{'='*60}")
        print(f"SUMMARY: {len(all_items)} total, {len(deprecated)} need attention, {len(supported)} OK")
        print(f"{'='*60}")
        
        for item in all_items:
            spec = item["service_specific"]
            icon = "🔴" if item["status"] == "end_of_life" else "🟡" if item["status"] == "deprecated" else "🟢"
            print(f"{icon} {spec['name']} ({spec['total_affected']} resources) - {item['status'].upper()}")
        
        return all_items


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="eu-west-1")
    parser.add_argument("--deprecated-only", action="store_true", help="Only show deprecated resources")
    args = parser.parse_args()
    
    discovery = AccountResourceDiscovery(region=args.region, include_supported=not args.deprecated_only)
    discovery.run()
