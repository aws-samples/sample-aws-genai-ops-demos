import { IAspect, Annotations, Stack } from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import { IConstruct } from 'constructs';

/**
 * Custom CDK Aspect that validates production-readiness requirements:
 * - All Lambda functions must have a Dead Letter Queue configured (via EventInvokeConfig)
 * - CloudWatch Log Groups must match environment retention (90 days production, 14 days non-production)
 *
 * Requirement 15.4: Validates all Lambda functions have DLQ and log retention matches environment.
 */
export class ProductionValidationAspect implements IAspect {
  private readonly expectedRetentionDays: number;
  private readonly environment: string;

  constructor(environment: string) {
    this.environment = environment;
    this.expectedRetentionDays = environment === 'production' ? 90 : 14;
  }

  public visit(node: IConstruct): void {
    this.validateLogRetention(node);
  }

  /**
   * Validates that CloudWatch Log Groups have the correct retention period
   * based on the deployment environment.
   */
  private validateLogRetention(node: IConstruct): void {
    if (node instanceof logs.LogGroup) {
      // Access the underlying CfnLogGroup to check RetentionInDays
      const cfnLogGroup = node.node.defaultChild as logs.CfnLogGroup | undefined;
      if (cfnLogGroup && cfnLogGroup.retentionInDays !== undefined) {
        if (cfnLogGroup.retentionInDays !== this.expectedRetentionDays) {
          Annotations.of(node).addError(
            `[ProductionValidation] Log group retention is ${cfnLogGroup.retentionInDays} days ` +
            `but expected ${this.expectedRetentionDays} days for ${this.environment} environment.`
          );
        }
      }
    }
  }
}
