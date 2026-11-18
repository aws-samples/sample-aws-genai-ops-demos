import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as eks from 'aws-cdk-lib/aws-eks';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3assets from 'aws-cdk-lib/aws-s3-assets';
import { KubectlV36Layer } from '@aws-cdk/lambda-layer-kubectl-v36';
import { Construct } from 'constructs';
import { getBedrockModelId, getCrisPrefix, getInferenceProfileRegion } from './bedrock-model';

// The chart pins the namespace and the service account name. Keep these two
// values in sync with helm/agatha/templates/serviceaccount.yaml.
const NAMESPACE = 'default';
const SERVICE_ACCOUNT = 'agatha-sa';

const DEFAULT_BASE_MODEL_ID = 'anthropic.claude-sonnet-4-5-20250929-v1:0';

export interface AgathaStackProps extends cdk.StackProps {
  /** Name of the existing EKS cluster to install Agatha onto. */
  clusterName: string;
  /** IAM role the CDK kubectl handler assumes. Must be cluster admin on the cluster. */
  kubectlRoleArn: string;
  /** OIDC issuer URL of the cluster, for example https://oidc.eks.<region>.amazonaws.com/id/<ID>. */
  oidcIssuer: string;
  /** Node CPU architecture the image is built for. Defaults to amd64. */
  nodeArchitecture?: string;
  /** Base Bedrock model id without the region prefix. */
  baseModelId?: string;
  /** Escape hatch: reference an existing image tag instead of building the image (used by destroy). */
  imageTag?: string;
}

export class AgathaStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AgathaStackProps) {
    super(scope, id, props);

    const region = this.region;
    const account = this.account;
    const partition = this.partition;

    // Bedrock model id with the region-aware prefix, passed to the pod as BEDROCK_MODEL.
    const baseModelId = props.baseModelId ?? DEFAULT_BASE_MODEL_ID;
    const crisPrefix = getCrisPrefix(region);
    const modelId = getBedrockModelId(baseModelId, region);
    const profileRegion = getInferenceProfileRegion(crisPrefix, partition, region);

    // Agent image. DockerImageAsset by default. With -c imageTag=<tag> the stack
    // references an image already pushed to the CDK bootstrap asset repository
    // (default qualifier), where DockerImageAsset stores it, so no Docker engine is
    // needed. deploy-all --destroy passes imageTag=destroy; that value is only used
    // to synthesize the template for deletion and is never deployed.
    let imageRepository: string;
    let imageTag: string;
    if (props.imageTag) {
      imageRepository = `${account}.dkr.ecr.${region}.${this.urlSuffix}/cdk-hnb659fds-container-assets-${account}-${region}`;
      imageTag = props.imageTag;
    } else {
      const arm = props.nodeArchitecture === 'arm64';
      const asset = new ecr_assets.DockerImageAsset(this, 'AgathaImage', {
        directory: path.join(__dirname, '../../../agent'),
        file: 'Dockerfile',
        platform: arm ? ecr_assets.Platform.LINUX_ARM64 : ecr_assets.Platform.LINUX_AMD64,
      });
      imageRepository = asset.repository.repositoryUri;
      imageTag = asset.imageTag;
    }

    // IRSA role for the agent pod. The trust policy is limited to the agent
    // service account, and the only permission is invoking one Bedrock model.
    const issuerHostPath = props.oidcIssuer.replace(/^https:\/\//, '');
    const oidcProvider = iam.OpenIdConnectProvider.fromOpenIdConnectProviderArn(
      this,
      'ClusterOidcProvider',
      `arn:${partition}:iam::${account}:oidc-provider/${issuerHostPath}`,
    );
    const agentRole = new iam.Role(this, 'AgathaAgentRole', {
      description: 'IRSA role for the Agatha EKS self-healing agent (Bedrock InvokeModel only)',
      assumedBy: new iam.OpenIdConnectPrincipal(oidcProvider, {
        StringEquals: {
          [`${issuerHostPath}:sub`]: `system:serviceaccount:${NAMESPACE}:${SERVICE_ACCOUNT}`,
          [`${issuerHostPath}:aud`]: 'sts.amazonaws.com',
        },
      }),
    });
    // Two ARN families for the one model: the foundation model (any region, to
    // cover cross-region inference) and the account-scoped inference profile.
    agentRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockInvokeModel',
        actions: ['bedrock:InvokeModel'],
        resources: [
          `arn:${partition}:bedrock:*::foundation-model/${baseModelId}`,
          `arn:${partition}:bedrock:${profileRegion}:${account}:inference-profile/${modelId}`,
        ],
      }),
    );

    // Existing cluster, referenced by name. The kubectl handler Lambda assumes
    // kubectlRoleArn to run helm against the cluster API endpoint.
    const cluster = eks.Cluster.fromClusterAttributes(this, 'Cluster', {
      clusterName: props.clusterName,
      kubectlRoleArn: props.kubectlRoleArn,
      kubectlLayer: new KubectlV36Layer(this, 'KubectlLayer'),
    });

    // Chart directory is zipped and uploaded as an asset, then installed by the handler.
    const chartAsset = new s3assets.Asset(this, 'ChartAsset', {
      path: path.join(__dirname, '../../../helm/agatha'),
    });
    const helmRelease = new eks.HelmChart(this, 'AgathaRelease', {
      cluster,
      chartAsset,
      release: 'agatha',
      namespace: NAMESPACE,
      createNamespace: false,
      values: {
        image: {
          repository: imageRepository,
          tag: imageTag,
          pullPolicy: 'IfNotPresent',
        },
        config: {
          awsRegion: region,
          bedrockModel: modelId,
        },
        serviceAccount: {
          name: SERVICE_ACCOUNT,
          irsaRoleArn: agentRole.roleArn,
        },
      },
    });
    helmRelease.node.addDependency(agentRole);

    new cdk.CfnOutput(this, 'ClusterName', { value: props.clusterName });
    new cdk.CfnOutput(this, 'KubectlRoleArn', { value: props.kubectlRoleArn });
    new cdk.CfnOutput(this, 'OidcIssuer', { value: props.oidcIssuer });
    new cdk.CfnOutput(this, 'AgentRoleArn', { value: agentRole.roleArn });
    new cdk.CfnOutput(this, 'ImageUri', { value: `${imageRepository}:${imageTag}` });
    new cdk.CfnOutput(this, 'BedrockModelId', { value: modelId });
    new cdk.CfnOutput(this, 'HelmRelease', { value: 'agatha' });
    new cdk.CfnOutput(this, 'Region', { value: region });
  }
}
