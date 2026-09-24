# =============================================================================
# frontend.tf - Static SPA hosting, S3 + CloudFront (CDK: frontend-stack.ts)
# =============================================================================
# Terraform creates the bucket and the distribution only. The build in
# ../frontend/dist needs the API URL and Cognito IDs, which exist after apply,
# so the deploy script builds the SPA, syncs it to the bucket and invalidates
# the distribution as a post-apply step (CDK does the same with a placeholder
# build followed by BucketDeployment).
# =============================================================================

resource "aws_s3_bucket" "website" {
  bucket_prefix = "aws-services-lifecycle-tracker-web-"
  force_destroy = true # demo: `terraform destroy` empties and removes the bucket
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "website" {
  bucket = aws_s3_bucket.website.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "website" {
  bucket = aws_s3_bucket.website.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_cloudfront_origin_access_control" "website" {
  name                              = "aws-services-lifecycle-tracker-oac"
  description                       = "OAC for the lifecycle tracker website bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "no-override"
  signing_protocol                  = "sigv4"
}

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_cloudfront_distribution" "website" {
  comment             = "AWS Services Lifecycle Tracker - Frontend Distribution"
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_All"

  origin {
    origin_id                = "website-bucket"
    domain_name              = aws_s3_bucket.website.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.website.id
  }

  default_cache_behavior {
    target_origin_id       = "website-bucket"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
  }

  # SPA routing: unknown paths (and S3's 403 for missing keys) serve index.html
  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = local.tags
}

# Grant CloudFront (this distribution only) read access to the bucket
resource "aws_s3_bucket_policy" "website" {
  bucket = aws_s3_bucket.website.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontServicePrincipalReadOnly"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.website.arn}/*"
      Condition = {
        StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.website.arn }
      }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.website]
}
