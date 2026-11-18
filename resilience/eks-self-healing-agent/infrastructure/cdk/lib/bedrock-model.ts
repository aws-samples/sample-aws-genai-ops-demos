// Bedrock model id helpers for the CDK stack.
//
// Mirrors get_bedrock_model_id() in shared/utils/aws_utils.py (us -> us, eu -> eu,
// ap -> apac, anything else -> global). Keep this file in sync with that util.
// shared/utils/aws-utils.ts has no model helper, so the mapping lives here.

const CRIS_PREFIX: Record<string, string> = { us: 'us', eu: 'eu', ap: 'apac' };

// A `global.` inference profile is addressed under the partition's home region.
const GLOBAL_PROFILE_HOME: Record<string, string> = {
  aws: 'us-east-1', // default home region of the global profile
  'aws-us-gov': 'us-gov-west-1',
  'aws-cn': 'cn-north-1',
};

export function getCrisPrefix(region: string): string {
  const geo = region.split('-')[0];
  return CRIS_PREFIX[geo] ?? 'global';
}

export function getBedrockModelId(baseModelId: string, region: string): string {
  return `${getCrisPrefix(region)}.${baseModelId}`;
}

export function getInferenceProfileRegion(prefix: string, partition: string, region: string): string {
  if (prefix !== 'global') {
    return region;
  }
  return GLOBAL_PROFILE_HOME[partition] ?? GLOBAL_PROFILE_HOME.aws;
}
