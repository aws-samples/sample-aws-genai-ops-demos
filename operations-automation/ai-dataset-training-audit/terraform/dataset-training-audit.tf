# =============================================================================
# dataset-training-audit.tf - the one stack-equivalent resource file
# =============================================================================
# Mirror of the single CDK stack DatasetTrainingAudit-${region}
# (../cdk/lib/dataset-training-audit-stack.ts). Same resource names, same IAM
# boundaries, same networking (no-NAT public-subnet VPC, Option B), so either
# IaC path produces an equivalent deployment. The two paths never co-deploy in
# one account+region.
# =============================================================================

# -----------------------------------------------------------------------------
# KMS key (shared by bucket, table, log group).
# -----------------------------------------------------------------------------
resource "aws_kms_key" "dart" {
  description             = "DART dataset-training-audit key (bucket, table, log group)"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  # Root account administration + CloudWatch Logs usage. The log-service
  # statement is required or a KMS-encrypted log group fails to create; it is
  # scoped to this account's log group ARNs via the EncryptionContext condition.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccount"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowCloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.region}.amazonaws.com" }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:*"
          }
        }
      },
    ]
  })

  tags = local.tags
}

resource "aws_kms_alias" "dart" {
  name          = "alias/dataset-training-audit-${var.region}"
  target_key_id = aws_kms_key.dart.key_id
}

# -----------------------------------------------------------------------------
# S3 output bucket.
# -----------------------------------------------------------------------------
resource "aws_s3_bucket" "output" {
  bucket        = "dart-agent-output-${local.account_id}-${var.region}"
  force_destroy = true # clean teardown (mirror of autoDeleteObjects)
  tags          = local.tags
}

resource "aws_s3_bucket_versioning" "output" {
  bucket = aws_s3_bucket.output.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "output" {
  bucket = aws_s3_bucket.output.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.dart.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "output" {
  bucket                  = aws_s3_bucket.output.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "output" {
  bucket = aws_s3_bucket.output.id
  rule {
    id     = "expire-old-reports"
    status = "Enabled"
    filter {
      prefix = "reports/"
    }
    expiration {
      days = 365
    }
  }
}

# enforceSSL — deny any non-TLS request.
resource "aws_s3_bucket_policy" "output" {
  bucket = aws_s3_bucket.output.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.output.arn,
        "${aws_s3_bucket.output.arn}/*",
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.output]
}

# -----------------------------------------------------------------------------
# DynamoDB audit table.
# -----------------------------------------------------------------------------
resource "aws_dynamodb_table" "audit" {
  name         = "dart-agent-audit-trail-${var.region}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_id"
  range_key    = "timestamp"

  attribute {
    name = "run_id"
    type = "S"
  }
  attribute {
    name = "timestamp"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.dart.arn
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.tags
}

# -----------------------------------------------------------------------------
# CloudWatch log group — managed explicitly so `terraform destroy` removes it.
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "task" {
  name              = "/dataset-training-audit/${var.region}"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.dart.arn
  tags              = local.tags
}

# -----------------------------------------------------------------------------
# VPC — no NAT, public subnets only, S3 + DynamoDB gateway endpoints (Option B).
# -----------------------------------------------------------------------------
data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "dart" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "dataset-training-audit-${var.region}" })
}

resource "aws_internet_gateway" "dart" {
  vpc_id = aws_vpc.dart.id
  tags   = local.tags
}

# Two public subnets (maxAzs: 2), /24 each, like the CDK public subnet group.
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.dart.id
  cidr_block              = "10.0.${count.index}.0/24"
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = merge(local.tags, { Name = "dataset-training-audit-public-${count.index}-${var.region}" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.dart.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.dart.id
  }
  tags = local.tags
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Free gateway endpoints for S3 and DynamoDB (no NAT needed for these).
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.dart.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]
  tags              = local.tags
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.dart.id
  service_name      = "com.amazonaws.${var.region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]
  tags              = local.tags
}

# -----------------------------------------------------------------------------
# Task security group — explicit, least-privilege: no ingress, egress 443 only.
# Identical to the CDK TaskSecurityGroup: the public-subnet task reaches Bedrock
# and Comprehend (not gateway-endpoint services) over the public internet, so
# 443 egress is load-bearing.
# -----------------------------------------------------------------------------
resource "aws_security_group" "task" {
  name        = "dataset-training-audit-task-${var.region}"
  description = "DART Fargate task - HTTPS egress only, no ingress"
  vpc_id      = aws_vpc.dart.id

  egress {
    description = "HTTPS egress for Bedrock and Comprehend"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.tags
}

# -----------------------------------------------------------------------------
# ECS cluster.
# -----------------------------------------------------------------------------
# Container Insights writes to /aws/ecs/containerinsights/<cluster>/performance
# and ECS creates that group itself if it is missing, outside Terraform, so it
# would survive `terraform destroy`. Manage it explicitly with the exact name
# (1-day retention, as ECS would) and create it before the cluster.
resource "aws_cloudwatch_log_group" "container_insights" {
  name              = "/aws/ecs/containerinsights/dataset-training-audit-${var.region}/performance"
  retention_in_days = 1
  kms_key_id        = aws_kms_key.dart.arn
  tags              = local.tags
}

resource "aws_ecs_cluster" "dart" {
  name       = "dataset-training-audit-${var.region}"
  depends_on = [aws_cloudwatch_log_group.container_insights]

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = local.tags
}

# -----------------------------------------------------------------------------
# ECR repository — the image the deploy script builds and pushes.
# force_delete so pushed images do not block `terraform destroy`.
# -----------------------------------------------------------------------------
resource "aws_ecr_repository" "dart" {
  name         = "dataset-training-audit-${var.region}"
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

# -----------------------------------------------------------------------------
# IAM — task execution role (pull image, write logs) and task role (app perms).
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "dataset-training-audit-exec-${var.region}"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Execution role must decrypt the log group's KMS key to write logs.
resource "aws_iam_role_policy" "execution_kms" {
  name = "LogGroupKmsDecrypt"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
      Resource = aws_kms_key.dart.arn
    }]
  })
}

resource "aws_iam_role" "task" {
  name               = "dataset-training-audit-task-${var.region}"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

# Task role policy — statements IDENTICAL to the CDK task role (design.md 4.1):
# two Bedrock ARN families per approved id, Comprehend *, X-Ray, own bucket /
# table / log group, KMS decrypt, and the conditional scoped input-bucket read.
data "aws_iam_policy_document" "task" {
  statement {
    sid       = "BedrockInvoke"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = local.bedrock_resources
  }

  statement {
    sid       = "ComprehendPii"
    actions   = ["comprehend:DetectPiiEntities"]
    resources = ["*"]
  }

  statement {
    sid       = "XRay"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }

  # Own output bucket (read + write).
  statement {
    sid = "OwnBucketReadWrite"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
      "s3:AbortMultipartUpload",
    ]
    resources = [
      aws_s3_bucket.output.arn,
      "${aws_s3_bucket.output.arn}/*",
    ]
  }

  # Own audit table (read + write).
  statement {
    sid = "OwnTableReadWrite"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:DescribeTable",
    ]
    resources = [
      aws_dynamodb_table.audit.arn,
      "${aws_dynamodb_table.audit.arn}/index/*",
    ]
  }

  # Own log group (write).
  statement {
    sid = "OwnLogGroupWrite"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.task.arn}:*"]
  }

  # KMS decrypt on the shared key.
  statement {
    sid = "KmsDecrypt"
    actions = [
      "kms:Decrypt",
      "kms:DescribeKey",
      "kms:Encrypt",
      "kms:GenerateDataKey",
      "kms:ReEncrypt*",
    ]
    resources = [aws_kms_key.dart.arn]
  }

  # Opt-in input-bucket read: one scoped statement per named bucket, never "*".
  dynamic "statement" {
    for_each = toset(var.input_buckets)
    content {
      sid     = "InputBucketRead${replace(statement.value, "/[^A-Za-z0-9]/", "")}"
      actions = ["s3:GetObject", "s3:ListBucket"]
      resources = [
        "arn:${local.partition}:s3:::${statement.value}",
        "arn:${local.partition}:s3:::${statement.value}/*",
      ]
    }
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "DartTaskPolicy"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# -----------------------------------------------------------------------------
# Fargate task definition.
# Image = "${ecr_repo_url}:${var.image_tag}"; var.image_tag has no default, so a
# bare apply fails at plan time (image build/push is owned by the deploy script).
# -----------------------------------------------------------------------------
resource "aws_ecs_task_definition" "dart" {
  family                   = "dataset-training-audit-${var.region}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "4096"
  memory                   = "8192"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "DartContainer"
      image     = "${aws_ecr_repository.dart.repository_url}:${var.image_tag}"
      essential = true
      environment = [
        { name = "AWS_REGION", value = var.region },
        { name = "AWS_ACCOUNT_ID", value = local.account_id },
        { name = "OUTPUT_BUCKET", value = aws_s3_bucket.output.id },
        { name = "AUDIT_TABLE", value = aws_dynamodb_table.audit.name },
        { name = "LOG_LEVEL", value = var.log_level },
        { name = "MODEL_ID", value = "${local.cris_prefix}.${var.model_id}" },
        { name = "MAX_TOKENS", value = "4096" },
        { name = "DEFAULT_MODE", value = "step-by-step" },
        { name = "DEDUP_SIMILARITY_THRESHOLD", value = "0.85" },
        { name = "ALLOWED_S3_BUCKETS", value = var.allowed_s3_buckets },
        { name = "ALLOWED_S3_PREFIXES", value = var.allowed_s3_prefixes },
        { name = "ALLOWED_LOCAL_BASE_DIR", value = var.allowed_local_base_dir },
      ]
      healthCheck = {
        command = ["CMD-SHELL", "python -c 'import agent' || exit 1"]
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.task.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "dart-agent"
        }
      }
    }
  ])

  tags = local.tags
}
