from aws_cdk import (
    Stack,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_s3_deployment as s3deploy,
    aws_lambda as _lambda,
    aws_apigateway as apigateway,
    aws_iam as iam,
    RemovalPolicy,
    CfnOutput
)
from constructs import Construct

class AnyCompanyITPortalStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # DynamoDB Tables for Mock Data
        
        # Tickets Table
        tickets_table = dynamodb.Table(
            self, "TicketsTable",
            table_name="anycompany-tickets",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            table_class=dynamodb.TableClass.STANDARD
        )
        tickets_table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING)
        )

        # Inventory Table
        inventory_table = dynamodb.Table(
            self, "InventoryTable",
            table_name="anycompany-inventory",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            table_class=dynamodb.TableClass.STANDARD
        )
        inventory_table.add_global_secondary_index(
            index_name="category-index",
            partition_key=dynamodb.Attribute(name="category", type=dynamodb.AttributeType.STRING)
        )

        # Purchase Orders Table
        purchase_orders_table = dynamodb.Table(
            self, "PurchaseOrdersTable",
            table_name="anycompany-purchase-orders",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            table_class=dynamodb.TableClass.STANDARD
        )
        purchase_orders_table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING)
        )

        # Assets Table
        assets_table = dynamodb.Table(
            self, "AssetsTable",
            table_name="anycompany-assets",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            table_class=dynamodb.TableClass.STANDARD
        )
        assets_table.add_global_secondary_index(
            index_name="location-index",
            partition_key=dynamodb.Attribute(name="location", type=dynamodb.AttributeType.STRING)
        )

        # Shipping Requests Table
        shipping_table = dynamodb.Table(
            self, "ShippingTable",
            table_name="anycompany-shipping",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            table_class=dynamodb.TableClass.STANDARD
        )
        shipping_table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING)
        )

        # Vendors Table
        vendors_table = dynamodb.Table(
            self, "VendorsTable",
            table_name="anycompany-vendors",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            table_class=dynamodb.TableClass.STANDARD
        )
        vendors_table.add_global_secondary_index(
            index_name="category-index",
            partition_key=dynamodb.Attribute(name="category", type=dynamodb.AttributeType.STRING)
        )

        # Models Table
        models_table = dynamodb.Table(
            self, "ModelsTable",
            table_name="anycompany-models",
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            table_class=dynamodb.TableClass.STANDARD
        )
        models_table.add_global_secondary_index(
            index_name="category-index",
            partition_key=dynamodb.Attribute(name="category", type=dynamodb.AttributeType.STRING)
        )
        models_table.add_global_secondary_index(
            index_name="manufacturer-index",
            partition_key=dynamodb.Attribute(name="manufacturer", type=dynamodb.AttributeType.STRING)
        )

        # S3 Bucket for Static Website Hosting
        website_bucket = s3.Bucket(
            self, "WebsiteBucket",
            bucket_name=f"anycompany-it-portal-{self.account}-{self.region}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            website_index_document="index.html",
            website_error_document="index.html"
        )

        # Lambda Function for API Backend
        api_lambda = _lambda.Function(
            self, "APILambda",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="index.handler",
            description="API backend for AnyCompany IT Portal - handles CRUD operations for all portal data",
            code=_lambda.Code.from_inline("""
import json
import boto3
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')

def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError

def handler(event, context):
    try:
        path = event.get('path', '')
        method = event.get('httpMethod', 'GET')
        
        # CORS headers
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
        }
        
        if method == 'OPTIONS':
            return {
                'statusCode': 200,
                'headers': headers,
                'body': ''
            }
        
        # Route requests to appropriate table
        if '/tickets' in path:
            table = dynamodb.Table('anycompany-tickets')
        elif '/inventory' in path:
            table = dynamodb.Table('anycompany-inventory')
        elif '/purchase-orders' in path:
            table = dynamodb.Table('anycompany-purchase-orders')
        elif '/assets' in path:
            table = dynamodb.Table('anycompany-assets')
        elif '/shipping' in path:
            table = dynamodb.Table('anycompany-shipping')
        elif '/vendors' in path:
            table = dynamodb.Table('anycompany-vendors')
        elif '/models' in path:
            table = dynamodb.Table('anycompany-models')
        else:
            return {
                'statusCode': 404,
                'headers': headers,
                'body': json.dumps({'error': 'Not found'})
            }
        
        if method == 'GET':
            response = table.scan()
            items = response.get('Items', [])
            return {
                'statusCode': 200,
                'headers': headers,
                'body': json.dumps(items, default=decimal_default)
            }
        elif method == 'POST':
            body = json.loads(event.get('body', '{}'))
            table.put_item(Item=body)
            return {
                'statusCode': 201,
                'headers': headers,
                'body': json.dumps({'message': 'Created successfully'})
            }
        elif method == 'PUT':
            body = json.loads(event.get('body', '{}'))
            table.put_item(Item=body)
            return {
                'statusCode': 200,
                'headers': headers,
                'body': json.dumps({'message': 'Updated successfully'})
            }
        
        return {
            'statusCode': 405,
            'headers': headers,
            'body': json.dumps({'error': 'Method not allowed'})
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': str(e)})
        }
            """),
            environment={
                'TICKETS_TABLE': tickets_table.table_name,
                'INVENTORY_TABLE': inventory_table.table_name,
                'PURCHASE_ORDERS_TABLE': purchase_orders_table.table_name,
                'ASSETS_TABLE': assets_table.table_name,
                'SHIPPING_TABLE': shipping_table.table_name,
                'VENDORS_TABLE': vendors_table.table_name,
                'MODELS_TABLE': models_table.table_name
            }
        )

        # Grant Lambda permissions to access DynamoDB tables
        tickets_table.grant_read_write_data(api_lambda)
        inventory_table.grant_read_write_data(api_lambda)
        purchase_orders_table.grant_read_write_data(api_lambda)
        assets_table.grant_read_write_data(api_lambda)
        shipping_table.grant_read_write_data(api_lambda)
        vendors_table.grant_read_write_data(api_lambda)
        models_table.grant_read_write_data(api_lambda)

        # API Gateway
        api = apigateway.RestApi(
            self, "ITPortalAPI",
            rest_api_name="AnyCompany IT Portal API",
            description="API for IT Portal Demo",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization"]
            )
        )

        # API Gateway Integration
        lambda_integration = apigateway.LambdaIntegration(api_lambda)

        # API Routes
        api.root.add_resource("tickets").add_method("ANY", lambda_integration)
        api.root.add_resource("inventory").add_method("ANY", lambda_integration)
        api.root.add_resource("purchase-orders").add_method("ANY", lambda_integration)
        api.root.add_resource("assets").add_method("ANY", lambda_integration)
        api.root.add_resource("shipping").add_method("ANY", lambda_integration)
        api.root.add_resource("vendors").add_method("ANY", lambda_integration)
        api.root.add_resource("models").add_method("ANY", lambda_integration)

        # Add /api/* routes for CloudFront routing
        api_resource = api.root.add_resource("api")
        api_resource.add_resource("tickets").add_method("ANY", lambda_integration)
        api_resource.add_resource("inventory").add_method("ANY", lambda_integration)
        api_resource.add_resource("purchase-orders").add_method("ANY", lambda_integration)
        api_resource.add_resource("assets").add_method("ANY", lambda_integration)
        api_resource.add_resource("shipping").add_method("ANY", lambda_integration)
        api_resource.add_resource("vendors").add_method("ANY", lambda_integration)
        api_resource.add_resource("models").add_method("ANY", lambda_integration)

        # CloudFront Distribution
        distribution = cloudfront.Distribution(
            self, "WebsiteDistribution",
            comment="AnyCompany IT Demo Portals - serves static HTML and API endpoints",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(website_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED
            ),
            additional_behaviors={
                "api/*": cloudfront.BehaviorOptions(
                    origin=origins.RestApiOrigin(api),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER
                )
            },
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html"
                )
            ]
        )

        # Outputs
        CfnOutput(
            self, "WebsiteURL",
            value=f"https://{distribution.distribution_domain_name}",
            description="Website URL"
        )

        CfnOutput(
            self, "APIEndpoint",
            value=api.url,
            description="API Gateway endpoint"
        )

        CfnOutput(
            self, "S3BucketName",
            value=website_bucket.bucket_name,
            description="S3 bucket for website hosting"
        )

        CfnOutput(
            self, "CloudFrontDistributionId",
            value=distribution.distribution_id,
            description="CloudFront distribution ID"
        )