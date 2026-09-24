"""Seed / refresh the service-extraction-config table from service_configs.json.

Deploy-time Lambda shared by both IaC paths, one source of truth:
  - CDK: inlined into the Data stack's custom resource (data-stack.ts reads
    this file at synth time); invoked with a CloudFormation custom-resource
    event (RequestType + ResourceProperties).
  - Terraform: packaged as a small function and run through
    aws_lambda_invocation; invoked with a plain {ServiceConfigs, TableName}
    payload.

Runtime/state fields are OWNED BY THE BACKEND (Lambda runtime) and must never
be written here (issue #116): a full-item put_item used to wipe extraction
history on every deploy, making services show as "Never extracted".
"""

import json
import os

import boto3

RUNTIME_FIELDS = {
    'extraction_count',
    'last_extraction',
    'success_rate',
    'last_refresh_origin',
    'last_extraction_duration',
}

# Fields seeded only when absent, so user changes (e.g. disabling a service in
# the UI) survive redeploys.
SEED_ONLY_FIELDS = {'enabled'}


def populate(services_config, table_name):
    """Upsert static, repo-owned fields and reconcile removed services.

    Static fields (documentation_urls, extraction_focus, ...) are updated on
    every run so config changes propagate. Backend-owned runtime fields are
    never written. update_item upserts, so new services are created.
    Returns (service_count, removed_count).
    """
    dynamodb = boto3.resource('dynamodb')
    config_table = dynamodb.Table(table_name)

    print(f"Populating {len(services_config)} service configurations into {table_name}...")

    for service_name, config in services_config.items():
        update_parts = []
        remove_parts = []
        expr_names = {}
        expr_values = {}
        # Evict runtime-state attributes that older deployments wrote into
        # config rows. They now live in the service-extraction-state table
        # (issue #116) and stale copies here would be a second, silently
        # diverging source of truth. REMOVE on an absent attribute is a
        # no-op, so this is safe on every deploy.
        for stale_field in sorted(RUNTIME_FIELDS):
            name_ph = f'#r{len(expr_names)}'
            expr_names[name_ph] = stale_field
            remove_parts.append(name_ph)
        for key, value in config.items():
            if key == 'service_name' or key in RUNTIME_FIELDS:
                continue
            # Placeholders are mandatory: field names like 'name' are
            # DynamoDB reserved words.
            name_ph = f'#f{len(expr_names)}'
            value_ph = f':v{len(expr_values)}'
            expr_names[name_ph] = key
            expr_values[value_ph] = value
            if key in SEED_ONLY_FIELDS:
                update_parts.append(f'{name_ph} = if_not_exists({name_ph}, {value_ph})')
            else:
                update_parts.append(f'{name_ph} = {value_ph}')

        if not update_parts:
            continue

        update_expression = 'SET ' + ', '.join(update_parts)
        if remove_parts:
            update_expression += ' REMOVE ' + ', '.join(remove_parts)

        config_table.update_item(
            Key={'service_name': service_name},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )
        print(f"OK {config.get('name', service_name)}: configuration saved (runtime state preserved)")

    # Reconcile: the JSON is the single source of truth for WHICH services
    # exist (issue #140). A service removed from service_configs.json must
    # disappear from the table too, otherwise it keeps being extracted.
    # Only the config table is touched; facts/inventory rows are the
    # backend's and are left for the next refresh / manual cleanup.
    removed = 0
    scan_kwargs = {'ProjectionExpression': 'service_name'}
    while True:
        page = config_table.scan(**scan_kwargs)
        for row in page.get('Items', []):
            if row['service_name'] not in services_config:
                config_table.delete_item(Key={'service_name': row['service_name']})
                removed += 1
                print(f"REMOVED {row['service_name']}: no longer in service_configs.json")
        if 'LastEvaluatedKey' not in page:
            break
        scan_kwargs['ExclusiveStartKey'] = page['LastEvaluatedKey']

    return len(services_config), removed


def handler(event, context):
    """Entry point for both the CloudFormation custom resource and Terraform."""
    if 'RequestType' in event:
        # CloudFormation custom resource (CDK path)
        if event['RequestType'] == 'Delete':
            return {
                'PhysicalResourceId': 'ServiceConfigPopulator',
                'Data': {'Message': 'Delete operation - no action needed'},
            }
        props = event['ResourceProperties']
        physical_id = 'ServiceConfigPopulator'
    else:
        # Plain invocation (Terraform aws_lambda_invocation)
        props = event
        physical_id = None

    services_config = json.loads(props['ServiceConfigs'])
    table_name = os.environ.get('CONFIG_TABLE_NAME') or props.get('TableName')
    print(f"Using table: {table_name}")

    count, removed = populate(services_config, table_name)
    result = {
        'Message': f'Populated {count} service configurations, removed {removed}',
        'ServiceCount': count,
        'RemovedCount': removed,
    }
    if physical_id:
        return {'PhysicalResourceId': physical_id, 'Data': result}
    return result
