import * as cdk from 'aws-cdk-lib';
import * as codecommit from 'aws-cdk-lib/aws-codecommit';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';
import * as path from 'path';

/**
 * MockFixtureStack — opt-in, self-contained test fixture for the demo.
 *
 * It stands up a CodeCommit repository seeded with three trivial handlers and
 * three Lambda functions on DEPRECATED runtimes (one each for .NET, Node.js and
 * Python). Each function is tagged so the demo's code-behind detection resolves
 * it to the fixture repository:
 *   - source-repo = lambda-migration-demo-fixture  (the repo tag; backend
 *     `resolve_repo_from_tags` reads tags[repo_tag], and the Settings UI default
 *     repo tag key is 'source-repo')
 *   - git-branch  = main                           (the branch tag; the backend
 *     reads tags[branch_tag], operator-configured in Settings)
 *
 * The seeded source for each language is the SAME directory the matching Lambda
 * uses for `code: lambda.Code.fromAsset(...)`, so "what is committed" equals
 * "what runs".
 *
 * NO solution-adoption tracking id/tag anywhere in this stack — only the
 * Pipeline stack (the demo's single tracked stack) carries (uksb-...)(tag:...).
 *
 * --- Runtime choice: "most deprecated that Lambda still permits CREATING" ---
 * python3.9, nodejs16.x and dotnet6 are all DEPRECATED today but their AWS
 * "block function create" date is in the future (Jul 29, 2027 at the time of
 * writing), so CloudFormation/CDK can still create them. These are AWS forecasts
 * and can move: if AWS brings a block-create date forward past "today", that
 * runtime's lambda.Function create will be rejected by the API and this mock
 * deploy fails — at which point bump the affected language to the next-newer
 * still-creatable runtime (nodejs18.x, python3.10/3.11, dotnet8).
 *
 * --- .NET asset is a discovery-only STUB ---
 * A real dotnet6 Lambda needs a compiled DLL + bootstrap, which we cannot
 * produce without a .NET build toolchain, and Docker bundling is not allowed.
 * The mock is meant to be DISCOVERED BY RUNTIME, never invoked, so the .NET
 * function ships the placeholder source under mock-fixtures/dotnet/ zipped via
 * lambda.Code.fromAsset(...). The create call validates the runtime id and that
 * a package exists; it does not validate the .NET handler. Python and Node ship
 * genuine trivial handlers.
 */

// Fixture repository name. The Pipeline and Transform stacks scope their
// CodeCommit grants to exactly this repository, so it is declared once here.
export const FIXTURE_REPO_NAME = 'lambda-migration-demo-fixture';

export class MockFixtureStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Fixture source trees live under cdk/mock-fixtures/<lang>/. path.join with
    // __dirname keeps the paths robust under ts-node (lib/ -> ../mock-fixtures).
    const fixturesRoot = path.join(__dirname, '..', 'mock-fixtures');
    const dotnetDir = path.join(fixturesRoot, 'dotnet');
    const nodejsDir = path.join(fixturesRoot, 'nodejs');
    const pythonDir = path.join(fixturesRoot, 'python');

    // (a) CodeCommit repository seeded (on branch `main`) from the parent
    // mock-fixtures/ directory, so the repo lands with dotnet/, nodejs/ and
    // python/ subfolders in one initial commit — the same sources the Lambdas
    // run below.
    const repo = new codecommit.Repository(this, 'FixtureRepo', {
      repositoryName: FIXTURE_REPO_NAME,
      description: 'Mock fixture repository holding the source for the three deprecated-runtime demo Lambda functions',
      code: codecommit.Code.fromDirectory(fixturesRoot, 'main'),
    });
    repo.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);

    // (b) Three Lambda functions on deprecated runtimes, one per language.
    // NO Docker bundling: each uses an inline asset from its fixture dir.
    const pythonFn = new lambda.Function(this, 'MockPython', {
      functionName: 'lambda-migration-mock-python',
      runtime: lambda.Runtime.PYTHON_3_9,
      handler: 'lambda_function.handler',
      code: lambda.Code.fromAsset(pythonDir),
      memorySize: 128,
      timeout: cdk.Duration.seconds(10),
    });
    pythonFn.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);

    const nodejsFn = new lambda.Function(this, 'MockNodejs', {
      functionName: 'lambda-migration-mock-nodejs',
      runtime: lambda.Runtime.NODEJS_16_X,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(nodejsDir),
      memorySize: 128,
      timeout: cdk.Duration.seconds(10),
    });
    nodejsFn.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);

    // .NET: discovery-only stub (see the stub note in the class comment). The
    // handler string is Assembly::Namespace.Class::Method; it is never invoked.
    const dotnetFn = new lambda.Function(this, 'MockDotnet', {
      functionName: 'lambda-migration-mock-dotnet',
      runtime: lambda.Runtime.DOTNET_6,
      handler: 'MockDotnet::MockDotnet.Function::Handler',
      code: lambda.Code.fromAsset(dotnetDir),
      memorySize: 128,
      timeout: cdk.Duration.seconds(10),
    });
    dotnetFn.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);

    // (c) Tags on EACH function so code-behind detection resolves the function
    // to the fixture repository. Keys confirmed against the code:
    // frontend constants.ts DEFAULT_REPO_TAG = 'source-repo'; backend reads
    // tags[repo_tag]/tags[branch_tag].
    for (const fn of [pythonFn, nodejsFn, dotnetFn]) {
      cdk.Tags.of(fn).add('source-repo', FIXTURE_REPO_NAME);
      cdk.Tags.of(fn).add('git-branch', 'main');
    }

    // (d) Outputs: repo HTTP clone URL + name, and each function name + ARN.
    new cdk.CfnOutput(this, 'FixtureRepoCloneUrlHttp', {
      value: repo.repositoryCloneUrlHttp,
      description: 'HTTP clone URL of the mock fixture CodeCommit repository',
    });
    new cdk.CfnOutput(this, 'FixtureRepoName', {
      value: repo.repositoryName,
      description: 'Name of the mock fixture CodeCommit repository',
    });

    new cdk.CfnOutput(this, 'MockPythonFunctionName', {
      value: pythonFn.functionName,
      description: 'Name of the deprecated-runtime (python3.9) mock Lambda function',
    });
    new cdk.CfnOutput(this, 'MockPythonFunctionArn', {
      value: pythonFn.functionArn,
      description: 'ARN of the deprecated-runtime (python3.9) mock Lambda function',
    });

    new cdk.CfnOutput(this, 'MockNodejsFunctionName', {
      value: nodejsFn.functionName,
      description: 'Name of the deprecated-runtime (nodejs16.x) mock Lambda function',
    });
    new cdk.CfnOutput(this, 'MockNodejsFunctionArn', {
      value: nodejsFn.functionArn,
      description: 'ARN of the deprecated-runtime (nodejs16.x) mock Lambda function',
    });

    new cdk.CfnOutput(this, 'MockDotnetFunctionName', {
      value: dotnetFn.functionName,
      description: 'Name of the deprecated-runtime (dotnet6) mock Lambda function',
    });
    new cdk.CfnOutput(this, 'MockDotnetFunctionArn', {
      value: dotnetFn.functionArn,
      description: 'ARN of the deprecated-runtime (dotnet6) mock Lambda function',
    });
  }
}
