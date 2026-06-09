import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';

/**
 * G.O.A.T. NetworkDataStack — provisions the dedicated Network_Data_Bucket
 * used by the Network Agent when no shared G.O.A.T. data bucket is available.
 *
 * The bucket reserves two top-level prefixes:
 *   - `raw/`     — VXLAN-encapsulated pcap files written by the
 *                  Traffic_Mirror_Collector EC2 instance.
 *   - `parquet/` — Transformed Parquet files produced by the
 *                  Transformation_Workflow Step Functions state machine.
 *
 * Lifecycle rules permanently delete `raw/` objects 7 days after creation
 * and `parquet/` objects 30 days after creation. Server-side encryption is
 * enabled with S3-managed keys and all forms of public access are blocked.
 *
 * The bucket name is exported as `GOATNetworkDataBucketName` so the
 * Network_Infra_Stack can import it via `cdk.Fn.importValue()`.
 *
 * This stack is instantiated conditionally by the CDK app — only when the
 * `GOATSharedDataBucketName` export is absent from the existing
 * `GOATData-${region}` stack. The stack ID at instantiation must be
 * `GOATNetworkData-${region}` (region suffix mandated by repository
 * conventions for multi-region deployments).
 *
 * Per Requirement 15.5 / 10.7, no solution-adoption-tracking marker is
 * applied to this stack — that marker lives only on the existing primary
 * G.O.A.T. orchestration runtime stack.
 *
 * Validates: Requirements 4.8, 4.9, 7.4, 7.5, 10.3, 15.6.
 */
export class NetworkDataStack extends cdk.Stack {
  /** S3 bucket designated as the Network_Data_Bucket. */
  public readonly networkDataBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // -----------------------------------------------------------------------
    // Network_Data_Bucket
    //
    //   - Server-side encryption: S3-managed keys (Req 7 — encryption enabled)
    //   - Public access: fully blocked (Req — public access blocked)
    //   - Lifecycle: raw/ deleted at +7 days (Req 4.8),
    //                parquet/ deleted at +30 days (Req 4.9)
    //
    // The `raw/` and `parquet/` prefixes are reserved exclusively for VXLAN
    // pcap files and transformed Parquet files respectively (Req 7.5). S3
    // does not require explicit prefix creation; lifecycle rules and the
    // collector / Step Functions clients enforce their use at runtime.
    // -----------------------------------------------------------------------
    this.networkDataBucket = new s3.Bucket(this, 'NetworkDataBucket', {
      bucketName: `goat-network-data-${this.account}-${this.region}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      lifecycleRules: [
        {
          id: 'DeleteRawPcapAfter7Days',
          enabled: true,
          prefix: 'raw/',
          expiration: cdk.Duration.days(7),
        },
        {
          id: 'DeleteParquetAfter30Days',
          enabled: true,
          prefix: 'parquet/',
          expiration: cdk.Duration.days(30),
        },
      ],
    });

    // -----------------------------------------------------------------------
    // Cross-stack export — consumed by Network_Infra_Stack via
    // cdk.Fn.importValue('GOATNetworkDataBucketName').
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'NetworkDataBucketName', {
      value: this.networkDataBucket.bucketName,
      description: 'S3 bucket holding raw/ pcap files and parquet/ transformed files for the G.O.A.T. Network Agent',
      exportName: 'GOATNetworkDataBucketName',
    });

    new cdk.CfnOutput(this, 'NetworkDataBucketArn', {
      value: this.networkDataBucket.bucketArn,
      description: 'ARN of the G.O.A.T. Network Agent data bucket',
      exportName: 'GOATNetworkDataBucketArn',
    });
  }
}
