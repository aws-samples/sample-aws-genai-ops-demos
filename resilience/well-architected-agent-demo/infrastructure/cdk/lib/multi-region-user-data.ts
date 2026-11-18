/**
 * Bootstrap script for the web tier: Apache plus a CGI page that increments a
 * counter in the DynamoDB global table. Only `region` and `table` are substituted; bash-only
 * references such as $COUNT and $(hostname) are left untouched.
 */
export function renderUserData(region: string, table: string): string {
  return `#!/bin/bash
set -euxo pipefail

# Install Apache. Package repos for Amazon Linux 2023 live in S3 and are reached
# through the S3 gateway VPC endpoint, so this works without a NAT/Internet gateway.
dnf install -y httpd

# Static landing page, also used as the ALB health check target
# (returns 200, does NOT touch DynamoDB).
cat > /var/www/html/index.html <<'HTML'
<!doctype html>
<html>
  <head><meta http-equiv="refresh" content="0; url=/cgi-bin/app.sh"></head>
  <body>Redirecting to the app...</body>
</html>
HTML

# CGI app: increments a visit counter in the DynamoDB global table and shows the
# global total. Uses the AWS CLI (pre-installed on AL2023) with the instance role
# credentials, reaching DynamoDB through the gateway VPC endpoint.
cat > /var/www/cgi-bin/app.sh <<'CGI'
#!/bin/bash
echo "Content-type: text/html"
echo ""

aws dynamodb update-item \\
  --region "${region}" \\
  --table-name "${table}" \\
  --key '{"pk":{"S":"visits"}}' \\
  --update-expression 'ADD hits :inc' \\
  --expression-attribute-values '{":inc":{"N":"1"}}' >/dev/null 2>&1 || true

COUNT=$(aws dynamodb get-item \\
  --region "${region}" \\
  --table-name "${table}" \\
  --key '{"pk":{"S":"visits"}}' \\
  --query 'Item.hits.N' --output text 2>/dev/null || echo "n/a")

echo "<!doctype html>"
echo "<html><body style='font-family:sans-serif;max-width:640px;margin:48px auto'>"
echo "<h1>Multi-region web app</h1>"
echo "<p>Served from region: <b>${region}</b></p>"
echo "<p>Instance: <b>$(hostname)</b></p>"
echo "<p>Total visits across all regions (DynamoDB global table): <b>$COUNT</b></p>"
echo "</body></html>"
CGI

chmod +x /var/www/cgi-bin/app.sh

systemctl enable httpd
systemctl start httpd
`;
}
