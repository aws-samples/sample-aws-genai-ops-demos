import * as cdk from 'aws-cdk-lib';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
// The CodeBuild role scopes its git permissions to exactly the fixture repo.
import { FIXTURE_REPO_NAME } from './mock-fixture-stack';
import { getTransformRegion } from '../../../../shared/utils/aws-utils';

export interface TransformStackProps extends cdk.StackProps {
  /** Name of the existing migration-artifacts bucket (DataStack.bucket). */
  bucketName: string;
  /** The migration orchestrator function (migrate.handler), created in
   *  PipelineStack. TransformStack grants it
   *  codebuild:StartBuild on the project and sets its PROJECT_NAME env var, so
   *  it needs the concrete Function (addEnvironment + addToRolePolicy). */
  orchestratorFunction: lambda.Function;
  /** The build_complete handler (build_complete.handler), created in
   *  PipelineStack. EventBridge invokes it on build
   *  terminal state. */
  buildCompleteFunction: lambda.IFunction;
}

// Buildspec object key under the artifacts bucket (uploaded by deploy-all).
// MUST be named buildspec.yml: with an S3 single-file source, CodeBuild
// downloads the object as the source root and then looks for a file literally
// named buildspec.yml within it. A differently-named object fails DOWNLOAD_SOURCE
// with "YAML file does not exist".
const BUILDSPEC_KEY = 'config/buildspec.yml';
// Fixed project name. Using the constant (not project.projectName /
// project.projectArn) to grant StartBuild and set PROJECT_NAME on the
// orchestrator keeps the only Pipeline<->Transform edge the EventBridge rule
// target (Transform -> Pipeline), so the graph stays acyclic. Referencing the
// project construct instead would make the orchestrator's role (in Pipeline)
// depend on a Transform output, forming a cycle with the rule target. Exported
// so the Pipeline stack can grant the API read access to the builds by name.
export const PROJECT_NAME = 'lambda-runtime-migration-transform';

/**
 * TransformStack — build plane for the migration (always deployed).
 *
 * Owns ONLY the resources that run AWS Transform inside CodeBuild and report
 * terminal state: a persistent CodeBuild project (source = the S3-hosted
 * buildspec), the CodeBuild IAM role (Transform + scoped S3/logs + CodeCommit
 * git access to the fixture repository), and an EventBridge rule on the build's
 * terminal state targeting the build_complete Lambda.
 *
 * The two migration Lambdas (orchestrator + build_complete) live in
 * PipelineStack so the API->orchestrator wiring stays
 * intra-stack; this stack depends on them one-directionally
 * (TransformStack -> PipelineStack), which is acyclic because nothing in
 * PipelineStack references a TransformStack output.
 *
 * NO solution-adoption tracking id/tag anywhere in this stack — only the
 * Pipeline stack (the demo's single tracked stack) carries (uksb-...)(tag:...).
 */
export class TransformStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: TransformStackProps) {
    super(scope, id, props);

    // Reuse the existing migration-artifacts bucket (DataStack.bucket); the
    // buildspec lives at config/ and per-run artifacts at migrations/<job_id>/.
    const bucket = s3.Bucket.fromBucketName(this, 'ArtifactsBucket', props.bucketName);

    const codecommitRepoArn =
      `arn:${this.partition}:codecommit:${this.region}:${this.account}:${FIXTURE_REPO_NAME}`;

    // ------------------------------------------------------------------
    // CodeBuild IAM role
    // ------------------------------------------------------------------
    const buildRole = new iam.Role(this, 'TransformBuildRole', {
      assumedBy: new iam.ServicePrincipal('codebuild.amazonaws.com'),
      description: 'CodeBuild role running AWS Transform (atx) and pushing the upgrade branch for the Lambda runtime migration',
    });

    // AWS Transform (atx) custom transformation grant: the one permission the
    // atx CLI needs to run a managed transformation from inside CodeBuild.
    buildRole.addToPolicy(new iam.PolicyStatement({
      sid: 'TransformCustom',
      actions: ['transform-custom:*'],
      resources: ['*'],
    }));

    // Scoped read/write on the artifacts bucket.
    bucket.grantReadWrite(buildRole);

    // CodeBuild log group writes.
    buildRole.addToPolicy(new iam.PolicyStatement({
      sid: 'CodeBuildLogs',
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/codebuild/*`],
    }));

    // CodeCommit git actions scoped to the single fixture repo, because the
    // buildspec clones it, cuts the upgrade branch and pushes it back. Git only
    // (clone, branch, push): the pushed upgrade
    // branch is the deliverable; no pull request is opened by anything in this
    // demo. No account-wide CodeCommit grant, no Bedrock.
    buildRole.addToPolicy(new iam.PolicyStatement({
      sid: 'CodeCommitGit',
      actions: [
        'codecommit:GitPull',
        'codecommit:GitPush',
        'codecommit:GetBranch',
        // The buildspec creates the branch locally and pushes it; whether GitPush alone authorizes a new ref is untested, so CreateBranch stays.
        'codecommit:CreateBranch',
        'codecommit:GetRepository',
      ],
      resources: [codecommitRepoArn],
    }));

    // ------------------------------------------------------------------
    // Persistent CodeBuild project (source = the S3-hosted buildspec)
    // ------------------------------------------------------------------
    const project = new codebuild.Project(this, 'TransformMigrateProject', {
      projectName: PROJECT_NAME,
      description: 'Runs AWS Transform (atx) against a target repository and pushes the upgrade branch for a Lambda runtime migration',
      role: buildRole,
      timeout: cdk.Duration.hours(8),
      source: codebuild.Source.s3({
        bucket,
        path: BUILDSPEC_KEY,
      }),
      environment: {
        buildImage: codebuild.LinuxBuildImage.AMAZON_LINUX_2_5,
        computeType: codebuild.ComputeType.MEDIUM,
        privileged: false,
      },
      // Default env vars; all overridden at start-build by the orchestrator.
      // The build tries AWS Transform in the DEPLOY region first and only uses
      // TRANSFORM_FALLBACK_REGION if atx reports the deploy region is not a
      // Transform region (there is no way to query Transform availability ahead
      // of time — see the buildspec).
      environmentVariables: {
        REPOSITORY_URL: { value: '' },
        TARGET_RUNTIME: { value: '' },
        TRANSFORM_NAME: { value: '' },
        // Default only: the orchestrator passes the resolved fallback on every
        // start-build. Same helper, same geo mapping as the Python side.
        TRANSFORM_FALLBACK_REGION: { value: getTransformRegion(this.region) },
        JOB_ID: { value: '' },
        OUTPUT_BUCKET: { value: props.bucketName },
      },
    });

    // The orchestrator calls codebuild:StartBuild on this project and needs its
    // name. Both grant and env var are set here (TransformStack owns the
    // project); this is the one-directional TransformStack -> PipelineStack edge.
    props.orchestratorFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'StartTransformBuild',
      actions: ['codebuild:StartBuild'],
      // Constructed from the fixed name (not project.projectArn) to avoid a
      // cross-stack ref back into this stack — see PROJECT_NAME note above.
      resources: [`arn:${this.partition}:codebuild:${this.region}:${this.account}:project/${PROJECT_NAME}`],
    }));
    props.orchestratorFunction.addEnvironment('PROJECT_NAME', PROJECT_NAME);

    // ------------------------------------------------------------------
    // EventBridge rule: build terminal state -> build_complete handler
    // ------------------------------------------------------------------
    new events.Rule(this, 'BuildStateChangeRule', {
      description: 'Invokes the build_complete handler when a Lambda runtime migration build reaches a terminal state',
      eventPattern: {
        source: ['aws.codebuild'],
        detailType: ['CodeBuild Build State Change'],
        detail: {
          'project-name': [project.projectName],
          'build-status': ['SUCCEEDED', 'FAILED', 'FAULT', 'STOPPED', 'TIMED_OUT'],
        },
      },
      targets: [new targets.LambdaFunction(props.buildCompleteFunction)],
    });

    // ------------------------------------------------------------------
    // Outputs (read by deploy-all)
    // ------------------------------------------------------------------
    new cdk.CfnOutput(this, 'TransformProjectName', {
      value: project.projectName,
      description: 'Name of the CodeBuild project running AWS Transform migrations',
    });
  }
}
