import { ArnFormat, CfnResource, Lazy, RemovalPolicy, Stack, aws_logs as logs } from 'aws-cdk-lib';

/**
 * Put the log group of a CDK-managed custom resource Lambda under stack control,
 * so `cdk destroy` leaves no orphaned log group behind. Shared by both variants.
 *
 * Declaring the log group is not enough: Lambda delivers logs a few seconds
 * after the invocation, so the custom resource run on destroy can recreate the
 * group after CloudFormation deleted it (seen in a real teardown). The fix is to
 * remove logs:CreateLogGroup from the Lambda role: late log events are then
 * dropped instead of recreating the group.
 *  - The function gets a fixed name, so the log group name and the policy ARN
 *    are plain strings and nothing references the function (no cycle).
 *  - The CDK default AWSLambdaBasicExecutionRole is removed. An inline policy
 *    allowing only CreateLogStream/PutLogEvents on this log group is APPENDED to
 *    the role's existing inline policies (some providers, e.g. the VPC default
 *    security group one, carry their own permissions there).
 *  - The function depends on the log group: created before, deleted after.
 *
 * @param stack          stack that owns the provider (providers are stack singletons)
 * @param providerId     construct id of the provider, e.g. 'Custom::S3AutoDeleteObjectsCustomResourceProvider'
 * @param functionName   fixed Lambda name (regional, max 64 characters)
 * @param logGroupId     construct id of the managed log group
 */
export function lockProviderLogGroup(
  stack: Stack,
  providerId: string,
  functionName: string,
  logGroupId: string,
): logs.LogGroup {
  const provider = stack.node.tryFindChild(providerId);
  const handler = provider?.node.tryFindChild('Handler') as CfnResource | undefined;
  const role = provider?.node.tryFindChild('Role') as CfnResource | undefined;
  // Fail the synth rather than silently ship an orphaning teardown if a future
  // aws-cdk-lib renames these internal constructs or changes their types.
  if (handler?.cfnResourceType !== 'AWS::Lambda::Function' || role?.cfnResourceType !== 'AWS::IAM::Role') {
    throw new Error(`${providerId} constructs not found: cannot manage their log group`);
  }
  if (functionName.length > 64) {
    throw new Error(`Lambda function name "${functionName}" exceeds 64 characters`);
  }

  const logGroupName = `/aws/lambda/${functionName}`;
  handler.addPropertyOverride('FunctionName', functionName);

  const logGroup = new logs.LogGroup(stack, logGroupId, {
    logGroupName,
    retention: logs.RetentionDays.ONE_MONTH,
    removalPolicy: RemovalPolicy.DESTROY,
  });
  handler.node.addDependency(logGroup);

  const writeOwnLogGroupOnly = {
    PolicyName: 'WriteOwnLogGroupOnly',
    PolicyDocument: {
      Version: '2012-10-17',
      Statement: [
        {
          // No logs:CreateLogGroup on purpose (see above).
          Effect: 'Allow',
          Action: ['logs:CreateLogStream', 'logs:PutLogEvents'],
          Resource: stack.formatArn({
            service: 'logs',
            resource: 'log-group',
            resourceName: `${logGroupName}:*`,
            arnFormat: ArnFormat.COLON_RESOURCE_NAME,
          }),
        },
      ],
    },
  };

  role.addPropertyDeletionOverride('ManagedPolicyArns');
  // Keep the provider's own inline policies (resolved at synth time) and append ours.
  // `_cfnProperties` is internal to CfnResource; the shape is checked so a future
  // change fails the synth instead of silently dropping the provider's permissions.
  role.addPropertyOverride(
    'Policies',
    Lazy.any({
      produce: () => {
        const raw = (role as unknown as { _cfnProperties?: Record<string, unknown> })._cfnProperties;
        if (!raw || typeof raw !== 'object') {
          throw new Error(`${providerId} role properties not readable: cannot append the log policy safely`);
        }
        const existing = stack.resolve(raw.Policies) ?? [];
        if (!Array.isArray(existing)) {
          throw new Error(`${providerId} role inline policies have an unexpected shape`);
        }
        return [...existing, writeOwnLogGroupOnly];
      },
    }),
  );
  return logGroup;
}
