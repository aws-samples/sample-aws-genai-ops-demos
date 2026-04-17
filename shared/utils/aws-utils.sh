#!/bin/bash
#
# Shared AWS utility functions for GenAI Ops demos.
# 
# Provides consistent region detection and AWS configuration across all demos.
#

##
# Detect AWS region using consistent priority order.
# 
# Priority:
# 1. AWS_DEFAULT_REGION environment variable (temporary override)
# 2. AWS_REGION environment variable (alternative)
# 3. AWS CLI configuration (aws configure get region)
# 4. Fallback to us-east-1 if nothing configured
# 
# Returns: AWS region name
##
get_aws_region() {
    local region=""
    
    # Check environment variables first
    if [ -n "$AWS_DEFAULT_REGION" ]; then
        region="$AWS_DEFAULT_REGION"
    elif [ -n "$AWS_REGION" ]; then
        region="$AWS_REGION"
    fi
    
    if [ -n "$region" ]; then
        echo "$region"
        return 0
    fi
    
    # Try AWS CLI configuration
    region=$(aws configure get region 2>/dev/null)
    if [ -n "$region" ]; then
        echo "$region"
        return 0
    fi
    
    # Fallback
    echo "us-east-1"
    return 0
}

##
# Get AWS account ID from current credentials.
# 
# Returns: AWS account ID or empty string if unable to determine
##
get_aws_account_id() {
    local account_id=""
    
    account_id=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
    if [ -n "$account_id" ]; then
        echo "$account_id"
        return 0
    fi
    
    return 1
}
