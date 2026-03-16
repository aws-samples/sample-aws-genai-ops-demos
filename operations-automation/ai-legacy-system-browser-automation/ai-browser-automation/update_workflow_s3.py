"""One-time script to update the Nova Act workflow definition with S3 export config.

Run this once to enable step data visualization in the AWS console.

Usage:
    python update_workflow_s3.py --bucket YOUR_BUCKET_NAME [--region us-east-1]
"""

import argparse
import os
import boto3
import sys


def update_workflow_definition(bucket_name: str, region: str = "us-east-1"):
    """Update the workflow definition with S3 export configuration."""
    
    workflow_name = "onboarding-email-workflow"
    
    # Create Nova Act client
    client = boto3.client("nova-act", region_name=region)
    
    # Check if workflow exists
    try:
        response = client.get_workflow_definition(workflowDefinitionName=workflow_name)
        print(f"Found existing workflow: {workflow_name}")
        print(f"  ARN: {response.get('arn')}")
        print(f"  Status: {response.get('status')}")
        
        existing_config = response.get('exportConfig', {})
        if existing_config.get('s3BucketName'):
            print(f"  Current S3 bucket: {existing_config.get('s3BucketName')}")
            print("Workflow already has S3 configuration.")
            return True
        
        # Delete and recreate with S3 config (Nova Act doesn't have update API)
        print(f"\nDeleting workflow to recreate with S3 config...")
        try:
            client.delete_workflow_definition(workflowDefinitionName=workflow_name)
            print("Workflow deleted successfully.")
        except Exception as e:
            print(f"Error deleting workflow: {e}")
            return False
        
        # Wait for deletion to propagate before recreating
        import threading
        deletion_event = threading.Event()
        deletion_event.wait(timeout=2)  # intentional delay for API deletion propagation
            
    except client.exceptions.ResourceNotFoundException:
        print(f"Workflow '{workflow_name}' not found. Creating it now...")
    
    # Create new workflow with S3 config
    print(f"\nCreating workflow with S3 bucket: {bucket_name}")
    try:
        response = client.create_workflow_definition(
            name=workflow_name,
            description="Onboarding email workflow for new employee equipment requests",
            exportConfig={
                "s3BucketName": bucket_name,
                "s3KeyPrefix": "workflow-data"
            }
        )
        print(f"Workflow created successfully!")
        print(f"  Status: {response.get('status')}")
        print(f"\nYou can now view step data in the AWS console.")
        return True
        
    except Exception as e:
        print(f"Error creating workflow: {e}")
        print("\nThe workflow will be recreated automatically on next run,")
        print("but without S3 config. You may need to run this script again.")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Update Nova Act workflow definition with S3 export config"
    )
    parser.add_argument(
        "--bucket", "-b",
        required=True,
        help="S3 bucket name for workflow data export"
    )
    parser.add_argument(
        "--region", "-r",
        default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1",
        help="AWS region (default: from AWS_REGION env var or us-east-1)"
    )
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Nova Act Workflow S3 Configuration Update")
    print("=" * 60)
    print(f"Bucket: {args.bucket}")
    print(f"Region: {args.region}")
    print()
    
    success = update_workflow_definition(args.bucket, args.region)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
