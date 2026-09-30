"""
Lifecycle canary: FSx publishes no metric for a file system's lifecycle, so this function
reads it every minute and publishes FileSystemMisconfigured (1 when the lifecycle is
MISCONFIGURED, MISCONFIGURED_UNAVAILABLE or FAILED, else 0) with a FileSystemId dimension.
The alarm on that metric is what starts the agent's investigation.

Environment: FILE_SYSTEM_ID, METRICS_NAMESPACE.
"""

import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

FILE_SYSTEM_ID = os.environ['FILE_SYSTEM_ID']
METRICS_NAMESPACE = os.environ['METRICS_NAMESPACE']
BROKEN = ('MISCONFIGURED', 'MISCONFIGURED_UNAVAILABLE', 'FAILED')

fsx = boto3.client('fsx')
cloudwatch = boto3.client('cloudwatch')


def handler(event, context):
    fs = fsx.describe_file_systems(FileSystemIds=[FILE_SYSTEM_ID])['FileSystems'][0]
    lifecycle = fs.get('Lifecycle', 'UNKNOWN')
    misconfigured = 1 if lifecycle in BROKEN else 0
    cloudwatch.put_metric_data(Namespace=METRICS_NAMESPACE, MetricData=[{
        'MetricName': 'FileSystemMisconfigured',
        'Dimensions': [{'Name': 'FileSystemId', 'Value': FILE_SYSTEM_ID}],
        'Value': misconfigured,
        'Unit': 'Count',
    }])
    detail = (fs.get('FailureDetails') or {}).get('Message', '')
    logger.info('%s lifecycle=%s misconfigured=%s %s', FILE_SYSTEM_ID, lifecycle, misconfigured, detail)
    return {'fileSystemId': FILE_SYSTEM_ID, 'lifecycle': lifecycle, 'misconfigured': misconfigured}
