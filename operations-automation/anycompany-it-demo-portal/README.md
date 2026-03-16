# AnyCompany IT Demo Portal

Multi-portal enterprise IT system simulation demonstrating AI-powered legacy system automation using Amazon Nova Act with AgentCore Browser Tool.

## Overview

This demo showcases how GenAI can automate workflows on legacy systems that lack modern APIs - a critical operational challenge for enterprises with aging IT infrastructure. The demo features authentic legacy system interfaces using static HTML with classic styling frameworks, demonstrating AI agents navigating different UI paradigms to complete complex IT workflows.

## Architecture

### Static HTML Multi-Portal Design
- **Main Portal Selector**: Modern CloudScape-based navigation interface
- **Three Legacy System Portals**: Static HTML pages simulating different legacy system interfaces
- **Backend Infrastructure**: AWS CDK with Amazon DynamoDB, AWS Lambda, Amazon API Gateway
- **Hosting**: Amazon S3 + Amazon CloudFront for global distribution

### Portal Systems

#### 1. IT Service Management Portal
- **Interface**: Professional workstation interface with clean styling
- **Functionality**: Create tickets, track progress, bulk operations, status updates
- **Use Case**: Central hub for IT service requests and incident management
- **AI Integration**: Extract ticket details, update status, manage workflows

#### 2. Inventory Management Portal
- **Interface**: Classic desktop interface with familiar UI elements
- **Functionality**: Stock tracking, item search/filter, availability checks, export
- **Use Case**: Hardware and software asset inventory management
- **AI Integration**: Check stock levels, reserve items, update quantities

#### 3. Procurement Management Portal
- **Interface**: Elegant desktop interface with streamlined design
- **Functionality**: Purchase order creation, vendor management, approval workflows
- **Use Case**: Procurement processes and vendor relationship management
- **AI Integration**: Create POs, submit for approval, track order status

## Technology Stack

- **Frontend**: Static HTML + JavaScript with authentic classic styling
  - Main Portal: AWS CloudScape Design System
  - Legacy Portals: Custom CSS frameworks for authentic legacy system appearance
- **Backend**: AWS Lambda (Python) + Amazon API Gateway for RESTful operations
- **Database**: Amazon DynamoDB with six tables for comprehensive data management
- **Hosting**: Amazon S3 static website hosting with Amazon CloudFront CDN
- **Infrastructure**: AWS CDK for automated deployment and management

## Project Structure

```
anycompany-it-demo-portal/
├── deploy-all.ps1                # PowerShell deployment script
├── deploy-all.sh                 # Bash deployment script
├── frontend/                     # Static HTML portal implementations
│   ├── index.html                # Main portal selector (CloudScape style)
│   ├── itsm.html                 # IT Service Management portal
│   ├── inventory.html            # Inventory management portal
│   ├── procurement.html          # Procurement management portal
│   └── css/                      # Custom CSS frameworks for legacy styling
├── infrastructure/cdk/           # AWS CDK infrastructure code
│   ├── app.py                    # CDK application entry point
│   ├── stack.py                  # Main infrastructure stack
│   └── requirements.txt          # Python dependencies
└── scripts/                      # Utility scripts
    ├── seed-data.py              # Mock data population script
    └── mock_data/                # JSON mock data files
```

## Quick Start

### Prerequisites
- **Python 3.11+** (for CDK and Lambda functions)
- **AWS CLI v2** configured with credentials
- **AWS CDK v2** installed globally

### One-Command Deployment

```powershell
# PowerShell (Windows)
cd operations-automation/anycompany-it-demo-portal
.\deploy-all.ps1 -PopulateData
```

```bash
# Bash (Linux/macOS)
cd operations-automation/anycompany-it-demo-portal
./deploy-all.sh --populate-data
```

**This deployment will:**
1. Deploy AWS infrastructure (Amazon DynamoDB, AWS Lambda, Amazon API Gateway, Amazon S3, Amazon CloudFront)
2. Upload static HTML portals to Amazon S3 and configure Amazon CloudFront distribution
3. Populate Amazon DynamoDB tables with realistic mock data
4. Provide the website URL for immediate testing

### Local Testing

Test the portals locally before deployment:

```bash
# Start local HTTP server
cd frontend
python -m http.server 8080

# Open portal selector in browser
open http://localhost:8080/index.html
```

**Local Test URLs:**
- **Portal Selector**: http://localhost:8080/index.html
- **ITSM Portal**: http://localhost:8080/itsm.html
- **Inventory Portal**: http://localhost:8080/inventory.html
- **Procurement Portal**: http://localhost:8080/procurement.html

## Demo Workflow

### AI Automation Scenario: Hardware Request Processing

**Trigger**: New employee hardware request email
```
Subject: Hardware Request - John Doe
Body: New employee John Doe (Engineering, Senior Developer) requires 
      complete hardware setup including Professional Laptop 16", 
      27" monitor, and peripherals. Start date: 2024-02-01. 
      Manager: Sarah Johnson. Budget approved under ENG-2024-Q1.
```

**AI Workflow Steps:**

1. **Read Service Request** (ITSM Portal)
   - Navigate to ITSM portal
   - Locate ticket `INC-001234`
   - Extract employee details and hardware requirements
   - Note budget code and approval status

2. **Check Inventory Availability** (Inventory Portal)
   - Navigate to inventory portal
   - Search for "Professional Laptop 16""
   - Verify stock level: 10 units available, 8 available after reservations
   - Check monitor and peripheral availability

3. **Create Purchase Order** (Procurement Portal - if needed)
   - Navigate to procurement portal
   - Create new PO for out-of-stock items
   - Select appropriate vendor (TechCorp Solutions)
   - Submit for approval workflow

4. **Update Service Request** (Return to ITSM Portal)
   - Navigate back to ITSM portal
   - Update ticket status to "In Progress" or "Completed"
   - Add completion notes with tracking information
   - Assign to appropriate team member

### Multi-Portal Navigation Pattern
```
Outlook Email → Mail Polling → Nova Act Browser Automation
  ↓                ↓              ↓
Detect email → Parse request → ITSM Portal → Inventory Portal → Procurement Portal → ITSM Portal
                                Create Ticket   Check Stock       Create PO (if needed)  Resolve Ticket
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    Amazon CloudFront Distribution                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                Amazon S3 Static Website                 │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────────┐   │   │
│  │  │Portal Select│ │ITSM Portal  │ │Inventory Portal │   │   │
│  │  │(CloudScape) │ │Service Desk │ │Asset Tracking   │   │   │
│  │  └─────────────┘ └─────────────┘ └─────────────────┘   │   │
│  │  ┌─────────────────┐                                   │   │
│  │  │Procurement      │                                   │   │
│  │  │Purchase Orders  │                                   │   │
│  │  └─────────────────┘                                   │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    API Gateway + Lambda                         │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  /api/tickets     /api/inventory    /api/purchase-orders│   │
│  │  /api/assets      /api/shipping     /api/vendors        │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                         DynamoDB Tables                         │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────────┐   │
│  │Tickets      │ │Inventory    │ │Purchase Orders          │   │
│  │- ID, Status │ │- Stock      │ │- PO Number, Status      │   │
│  │- Priority   │ │- Available  │ │- Vendor, Amount         │   │
│  └─────────────┘ └─────────────┘ └─────────────────────────┘   │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────────┐   │
│  │Assets       │ │Shipping     │ │Vendors                  │   │
│  │- Asset Tag  │ │- Tracking   │ │- Contact Info           │   │
│  │- Assignment │ │- Delivery   │ │- Payment Terms          │   │
│  └─────────────┘ └─────────────┘ └─────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Mock Data

The demo includes comprehensive realistic mock data:

### IT Service Management (20 tickets)
- **Hardware Requests**: Employee laptop setups, monitor requests, peripheral needs
- **Access Requests**: AWS Console access, system permissions, account provisioning  
- **Network Issues**: Connectivity problems, infrastructure outages, performance issues
- **Software Requests**: Application installations, license management, updates
- **Incidents**: System outages, security alerts, critical infrastructure issues

### Inventory Management (7 items)
- **Laptops**: Professional Laptop 16", Professional Series 15"
- **Monitors**: UltraSharp 27" 4K, UltraWide 34"
- **Peripherals**: Wireless Mouse & Keyboard Combo
- **Mobile**: Business Smartphone Pro
- **Networking**: Enterprise Switch 24-Port

### Procurement Management (2 purchase orders)
- **PO-2024-001**: Professional Laptops (Approved, $3,798)
- **PO-2024-002**: Business Laptop Pro (Submitted, $2,499)

### Vendor Management (3 active vendors)
- **TechCorp Solutions**: Hardware vendor with Net 30 terms
- **Premium Computing Inc.**: Business computing with Net 15 terms
- **Peripheral Systems Ltd.**: Accessories vendor with Net 30 terms

## AI Integration with Amazon Nova Act

### AgentCore Browser Tool Integration

The browser automation uses Nova Act with AgentCore Browser Tool to navigate the portals. All browser actions are defined in JSON workflow definitions and executed dynamically.

```python
# Real workflow execution pattern (simplified)
from bedrock_agentcore.tools.browser_client import browser_session
from nova_act import NovaAct
from nova_act.types.workflow import workflow

@workflow(workflow_definition_name="onboarding-email-workflow", model_id="nova-act-latest")
def hardware_provisioning_workflow():
    with browser_session("us-east-1", identifier="your-browser-id") as client:
        ws_url, headers = client.generate_ws_headers()
        
        with NovaAct(
            cdp_endpoint_url=ws_url,
            cdp_headers=headers,
            starting_page="https://your-domain.cloudfront.net/itsm.html",
        ) as nova:
            # Step 1: Create ITSM ticket
            nova.act("Click the 'Create Ticket' button")
            nova.act("Fill out the form with Title: 'Hardware Request - John Doe'...")
            nova.act("Click 'Create Ticket' to submit")
            
            # Step 2: Check inventory
            nova.act("Navigate to https://your-domain.cloudfront.net/inventory.html")
            nova.act("Search for 'Professional Laptop 16'")
            
            # Step 3: Create purchase order if needed
            nova.act("Navigate to https://your-domain.cloudfront.net/procurement.html")
            nova.act("Click 'Create Purchase Order' and fill the form...")
            
            # Step 4: Update ticket to resolved
            nova.act("Navigate to https://your-domain.cloudfront.net/itsm.html")
            nova.act("Update ticket status to Resolved")
```

See the [ai-legacy-system-browser-automation](../ai-legacy-system-browser-automation/) component for the full implementation with JSON-driven workflow definitions.

### API Integration for Programmatic Access

```bash
# Direct API access for backend integration
BASE_URL="https://your-api-gateway-url"

# Get all service tickets
curl "$BASE_URL/api/tickets" \
  -H "Content-Type: application/json"

# Check inventory for specific item
curl "$BASE_URL/api/inventory" \
  -H "Content-Type: application/json" \
  | jq '.[] | select(.name | contains("Professional Laptop"))'

# Create new purchase order
curl -X POST "$BASE_URL/api/purchase-orders" \
  -H "Content-Type: application/json" \
  -d '{
    "vendorId": "vendor-1",
    "items": [{"itemName": "Professional Laptop 16", "quantity": 1, "unitPrice": 2499.00}],
    "budgetCode": "ENG-2024-Q1"
  }'

# Update ticket status
curl -X PUT "$BASE_URL/api/tickets/INC-001234" \
  -H "Content-Type: application/json" \
  -d '{"status": "Completed", "notes": "Hardware provisioned successfully"}'
```

## Cost Estimates

### Demo Environment (Monthly)
- **DynamoDB**: $1-5 (pay-per-request pricing, minimal usage)
- **Lambda**: $0.20-1 (API request processing, 1M requests/month)
- **API Gateway**: $1-3 (REST API calls, standard pricing)
- **S3**: $0.50-2 (static website hosting, minimal storage)
- **CloudFront**: $0.50-2 (CDN distribution, low traffic)
- **Total Estimated Cost**: $3-13/month

### Production Scaling Considerations
- **Authentication** (AWS Cognito): +$0-5/month for user management
- **Enhanced Monitoring** (CloudWatch): +$2-10/month for detailed metrics
- **Backup Strategy** (DynamoDB backups): +$1-5/month for data protection
- **WAF Protection**: +$5-20/month for web application firewall
- **Multi-Region Deployment**: +50-100% for high availability

## Deployment Outputs

After successful deployment, you'll receive:

```
=== Deployment Complete ===

🌐 Website URL: https://d1234567890abc.cloudfront.net
🔗 API Endpoint: https://abcd1234.execute-api.us-east-1.amazonaws.com/prod
📦 S3 Bucket: anycompany-it-portal-123456789012-us-east-1
🚀 CloudFront Distribution: E1234567890ABC

Next Steps:
1. Open the website URL to access the IT Portal Demo
2. Navigate between different portals to see the mock data
3. Use this environment for AI automation testing
```

## Testing and Validation

### Manual Testing Checklist
- [ ] **Portal Selector**: CloudScape styling loads correctly
- [ ] **ITSM Portal**: Interface displays properly, forms work
- [ ] **Inventory Portal**: Interface authentic, search/filter functional
- [ ] **Procurement Portal**: Interface correct, PO creation works
- [ ] **Navigation**: Back buttons return to portal selector
- [ ] **Responsive**: Works on different screen sizes
- [ ] **Title Bar Buttons**: Minimize, maximize, close display correctly

### Automated Testing
```bash
# Test all portal endpoints
curl -f http://localhost:8080/index.html
curl -f http://localhost:8080/itsm.html
curl -f http://localhost:8080/inventory.html
curl -f http://localhost:8080/procurement.html

# Validate CSS resources load
curl -f http://localhost:8080/css/itsm.css
curl -f http://localhost:8080/css/inventory.css
curl -f http://localhost:8080/css/procurement.css
```

## Cleanup

To destroy all AWS resources and avoid ongoing charges:

```powershell
# PowerShell (Windows)
.\deploy-all.ps1 -DestroyInfra
```

```bash
# Bash (Linux/macOS)
./deploy-all.sh --destroy-infra
```

This will remove all CloudFormation stacks, S3 buckets, DynamoDB tables, and associated resources.

## Troubleshooting

### Common Deployment Issues

**CDK Bootstrap Required**
```bash
# Solution: Bootstrap CDK in your account/region
npx cdk bootstrap aws://ACCOUNT-ID/REGION
```

**Classic Stylesheets Missing**
```bash
# Solution: Ensure CSS files are available
ls frontend/css/
# Should show: cde.css, ventana.css, manzana.css
```

**DynamoDB Access Denied**
```bash
# Solution: Check Lambda IAM permissions in CloudFormation
aws cloudformation describe-stack-resources --stack-name AnyCompanyITPortalStack
```

**CloudFront Cache Issues**
```bash
# Solution: Invalidate CloudFront distribution
aws cloudfront create-invalidation --distribution-id YOUR-DISTRIBUTION-ID --paths "/*"
```

### Monitoring and Logs
- **Lambda Logs**: CloudWatch `/aws/lambda/AnyCompanyITPortalStack-APILambda`
- **API Gateway Logs**: Enable in API Gateway console for detailed request tracking
- **DynamoDB Metrics**: Monitor read/write capacity and throttling
- **CloudFront Metrics**: Track cache hit ratio and origin requests

## Development and Customization

### Adding New Portals
1. Create new HTML file in `frontend/`
2. Choose appropriate CSS styling approach
3. Add navigation link in `index.html`
4. Update CDK stack with new DynamoDB table if needed
5. Extend Lambda function for new API endpoints

### Customizing Themes
- **ITSM Portal**: Modify `frontend/css/cde.css`
- **Inventory Portal**: Customize `frontend/css/ventana.css`
- **Procurement Portal**: Adjust `frontend/css/manzana.css`

### Backend API Extensions
```python
# Add new endpoint in Lambda function
def handle_new_endpoint(event, context):
    # Custom business logic
    return {
        'statusCode': 200,
        'headers': cors_headers,
        'body': json.dumps(response_data)
    }
```

## Configuration Management

### Frontend Configuration
The demo uses runtime configuration generation to avoid hardcoded environment-specific values:

- **Development**: Deployment scripts generate `frontend/config.js` with actual API endpoints
- **Fallback**: HTML files include fallback URLs for local development
- **Gitignore**: `config.js` is excluded from version control to prevent environment-specific commits

### API Configuration Pattern
```javascript
// Runtime configuration loading
let apiBaseUrl = window.APP_CONFIG ? window.APP_CONFIG.apiBaseUrl : 'fallback-url';
```

This approach ensures:
- ✅ **Cross-account compatibility**: Works in any AWS account/region
- ✅ **Professional deployment**: No hardcoded environment values
- ✅ **Consistent user experience**: Same configuration approach across demos

## Solution Adoption Tracking

This demo implements solution adoption tracking with:
- **Tracking ID**: `uksb-do9bhieqqh`
- **Tags**: `(tag:it-portal-demo,operations-automation)`

This enables measurement of usage and adoption patterns through the AWS Solution Adoption Dashboard for data-driven roadmap planning.

## Resources and References

- **AWS CloudScape**: [Design System Documentation](https://cloudscape.design/)
- **Amazon Nova Act**: [AI Agent Documentation](https://aws.amazon.com/nova/act/)
- **AgentCore Browser Tool**: [Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/)
- **AWS CDK**: [Developer Guide](https://docs.aws.amazon.com/cdk/)
- **DynamoDB**: [Developer Guide](https://docs.aws.amazon.com/dynamodb/)

---

**Demo Category**: Operations Automation  
**Primary Technologies**: Amazon Nova Act, AgentCore Browser Tool, Static HTML, Classic UI Frameworks  
**Infrastructure**: AWS CDK, DynamoDB, Lambda, API Gateway, S3, CloudFront  
**Deployment Time**: 5-10 minutes  
**Estimated Monthly Cost**: $3-13 USD

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
