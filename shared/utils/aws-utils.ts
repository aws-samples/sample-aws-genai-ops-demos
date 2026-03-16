/**
 * Shared AWS utility functions for GenAI Ops demos.
 * 
 * Provides consistent region detection and AWS configuration across all demos.
 */

import { execSync } from 'child_process';

/**
 * Detect AWS region using consistent priority order.
 * 
 * Priority:
 * 1. AWS_DEFAULT_REGION environment variable (temporary override)
 * 2. AWS_REGION environment variable (alternative)
 * 3. AWS CLI configuration (aws configure get region)
 * 4. Fallback to us-east-1 if nothing configured
 * 
 * @returns AWS region name
 */
export function getRegion(): string {
  // Check environment variables first
  let region = process.env.AWS_DEFAULT_REGION || process.env.AWS_REGION;
  
  if (region) {
    return region;
  }
  
  // Try AWS CLI configuration
  try {
    region = execSync('aws configure get region', { encoding: 'utf-8' }).trim();
    if (region) {
      return region;
    }
  } catch {
    // AWS CLI not available or no region configured
  }
  
  // Fallback
  return 'us-east-1';
}

/**
 * Get AWS account ID from current credentials.
 * 
 * @returns AWS account ID or null if unable to determine
 */
export function getAccountId(): string | null {
  try {
    const accountId = execSync('aws sts get-caller-identity --query Account --output text', {
      encoding: 'utf-8'
    }).trim();
    return accountId || null;
  } catch {
    return null;
  }
}
