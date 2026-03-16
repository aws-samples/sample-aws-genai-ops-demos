# AnyCompany IT Portal - Technical Architecture

## Overview

The AnyCompany IT Portal Demo demonstrates AI-powered legacy system automation using Amazon Nova Act with AgentCore Browser Tool. It showcases how GenAI can automate workflows on legacy systems that lack modern APIs through a static HTML multi-portal architecture featuring authentic legacy system interfaces.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Static HTML Multi-Portal Architecture                │
│                                                                              │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │   Portal Selector   │  │   ITSM Portal       │  │   Inventory Portal  │  │
│  │   (CloudScape)      │  │   (Service Desk)    │  │   (Asset Tracking)  │  │
│  │                     │  │                     │  │                     │  │
│  │ • Modern UI         │  │ • Professional UI   │  │ • Legacy Interface  │  │
│  │ • Navigation Cards  │  │ • Ticket Management │  │ • Stock Tracking    │  │
│  │ • Responsive        │  │ • Status Updates    │  │ • Asset Search      │  │
│  │ • index.html        │  │ • itsm.html         │  │ • inventory.html    │  │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘  │
│                                                                              │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │ Procurement Portal  │  │   CSS Framework     │  │                     │  │
│  │   (Purchase Orders) │  │   (Legacy Styling)  │  │                     │  │
│  │                     │  │                     │  │                     │  │
│  │ • Legacy Interface  │  │ • Custom Themes     │  │                     │  │
│  │ • Purchase Orders   │  │ • Authentic Look    │  │                     │  │
│  │ • Vendor Mgmt       │  │ • Cross-browser     │  │                     │  │
│  │ • procurement.html  │  │ • Responsive        │  │                     │  │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘  │
│                                     │                                        │
└─────────────────────────────────────┼───────────────────────────────────────┘
                                      │ HTTPS
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS Cloud Infrastructure                        │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  CloudFront Distribution                                              │  │
│  │  • Static HTML delivery                                               │  │
│  │  • Global CDN for CSS/JS assets                                       │  │
│  │  • HTTPS termination                                                  │  │
│  │  • API Gateway integration (/api/*)                                   │  │
│  └──────────────────────────────┬────────────────────────────────────────┘  │
│                                 │                                            │
│  ┌──────────────────────────────┴────────────────────────────────────────┐  │
│  │  S3 Static Website Hosting                                            │  │
│  │  • index.html (Portal Selector)                                       │  │
│  │  • itsm.html (IT Service Management portal)                          │  │
│  │  • inventory.html (Inventory management portal)                      │  │
│  │  • procurement.html (Procurement management portal)                  │  │
│  │  • css/ (Custom CSS framework)                                       │  │
│  └──────────────────────────────┬────────────────────────────────────────┘  │
│                                 │                                            │
│  ┌──────────────────────────────┴────────────────────────────────────────┐  │
│  │  API Gateway                                                          │  │
│  │  • RESTful API endpoints                                              │  │
│  │  • CORS configuration                                                 │  │
│  │  • Lambda integration                                                 │  │
│  │  • /api/tickets, /api/inventory, /api/purchase-orders                │  │
│  └──────────────────────────────┬────────────────────────────────────────┘  │
│                                 │                                            │
│  ┌──────────────────────────────┴────────────────────────────────────────┐  │
│  │  Lambda Function (Python 3.11)                                       │  │
│  │  • API request handling                                               │  │
│  │  • DynamoDB CRUD operations                                           │  │
│  │  • Business logic processing                                          │  │
│  │  • CORS headers management                                            │  │
│  └──────────────────────────────┬────────────────────────────────────────┘  │
│                                 │                                            │
│  ┌──────────────────────────────┴────────────────────────────────────────┐  │
│  │  DynamoDB Tables (Pay-per-Request)                                   │  │
│  │  • anycompany-tickets (Service requests & incidents)                 │  │
│  │  • anycompany-inventory (Hardware & software assets)                 │  │
│  │  • anycompany-purchase-orders (Procurement workflows)                │  │
│  │  • anycompany-assets (Asset tracking & assignment)                   │  │
│  │  • anycompany-shipping (Delivery & logistics)                        │  │
│  │  • anycompany-vendors (Supplier management)                          │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Static HTML Architecture

### Design Philosophy
The demo uses static HTML files with custom CSS frameworks to create authentic recreations of legacy system interfaces. This approach eliminates React complexity while maintaining visual authenticity and full functionality.

### Portal Implementation Strategy

#### 1. Portal Selector (index.html)
- **Framework**: Pure HTML + CSS with CloudScape styling
- **Purpose**: Modern navigation interface to access legacy portals
- **Features**: Responsive cards, workflow overview, professional appearance
- **Technology**: AWS CloudScape Design System via CDN

#### 2. ITSM Portal (itsm.html)
- **Interface**: Professional workstation interface
- **Styling**: `frontend/css/cde.css`
- **Features**: Ticket management, status updates, bulk operations
- **Target Use Case**: IT service management and incident tracking

#### 3. Inventory Portal (inventory.html)
- **Interface**: Classic desktop interface
- **Styling**: `frontend/css/ventana.css`
- **Features**: Stock tracking, search/filter, availability checks
- **Target Use Case**: Hardware and software inventory management

#### 4. Procurement Portal (procurement.html)
- **Interface**: Elegant desktop interface
- **Styling**: `frontend/css/manzana.css`
- **Features**: Purchase order creation, vendor management, approval workflows
- **Target Use Case**: Procurement processes and supplier relationships

### CSS Framework Integration

#### CSS Framework Structure
```
frontend/css/
├── cde.css                       # ITSM portal styling
├── ventana.css                   # Inventory portal styling
└── manzana.css                   # Procurement portal styling
```

#### HTML Component Patterns
```html
<!-- Standard window structure across all portals -->
<div class="window active">
    <div class="title-bar">
        <div class="title-bar-text">Portal Title</div>
        <div class="title-bar-buttons">
            <button data-minimize></button>
            <button data-maximize></button>
            <button data-close></button>
        </div>
    </div>
    <div class="window-body padding">
        <!-- Portal content -->
    </div>
</div>
```

#### Frontend Technology Stack

#### Core Technologies
- **HTML5**: Semantic markup with accessibility considerations
- **CSS3**: Custom CSS frameworks for authentic legacy system styling
- **JavaScript (ES6+)**: Vanilla JavaScript for interactivity
- **No Build Process**: Direct deployment of source files

#### Styling Approach
- **Custom CSS**: Individual CSS files for authentic legacy system appearance
- **Responsive Design**: Viewport-based sizing for full-screen experience
- **Cross-Browser**: Compatible with modern browsers

#### JavaScript Functionality
- **DOM Manipulation**: Direct element selection and modification
- **Event Handling**: Click, form submission, and navigation events
- **Data Management**: In-memory JavaScript objects for mock data
- **API Integration**: Fetch API for backend communication (future)

### Backend Architecture

#### API Gateway Configuration
- **Type**: REST API with CORS enabled
- **Base Path**: `/api`
- **Authentication**: None (demo environment)
- **Endpoints**:
  ```
  GET/POST /api/tickets          # Service ticket operations
  GET/POST /api/inventory        # Inventory management
  GET/POST /api/purchase-orders  # Procurement workflows
  GET/POST /api/assets          # Asset tracking
  GET/POST /api/shipping        # Delivery coordination
  GET/POST /api/vendors         # Vendor management
  ```

#### Lambda Function Design
```python
# Lambda handler structure
def handler(event, context):
    """
    Main API handler for all portal operations
    Routes requests based on path and HTTP method
    """
    path = event.get('path', '')
    method = event.get('httpMethod', 'GET')
    
    # CORS headers for all responses
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
    }
    
    # Route to appropriate handler
    if '/tickets' in path:
        return handle_tickets(event, context, headers)
    elif '/inventory' in path:
        return handle_inventory(event, context, headers)
    # ... additional routing logic
```

#### DynamoDB Schema Design

##### Table Design Principles
- **Simple Partition Key**: Single `id` attribute for demo simplicity
- **Pay-per-Request**: Automatic scaling without capacity planning
- **Point-in-Time Recovery**: Enabled for data protection
- **Global Secondary Indexes**: Added for common query patterns

##### Table Specifications

**anycompany-tickets**
```json
{
  "id": "INC-001234",
  "title": "Hardware Request - John Doe",
  "description": "New employee hardware setup requirements",
  "status": "Open",
  "priority": "High",
  "assignee": "IT Admin",
  "requester": "Sarah Johnson",
  "category": "Hardware Request",
  "createdDate": "2024-01-25",
  "updatedDate": "2024-01-25"
}
```

**anycompany-inventory**
```json
{
  "id": "laptop-001",
  "name": "Professional Laptop 16\"",
  "category": "Laptops",
  "manufacturer": "TechCorp",
  "model": "Professional Series",
  "stockLevel": 10,
  "availableQuantity": 8,
  "unitCost": 2499.00,
  "location": "IT Storage Room A"
}
```

**anycompany-purchase-orders**
```json
{
  "id": "PO-2024-001",
  "vendorId": "vendor-1",
  "vendorName": "TechCorp Solutions",
  "items": [
    {
      "itemName": "Professional Laptop 15\"",
      "quantity": 2,
      "unitPrice": 1899.00,
      "totalPrice": 3798.00
    }
  ],
  "totalAmount": 3798.00,
  "status": "Approved",
  "budgetCode": "IT-2024-Q1"
}
```

### Infrastructure as Code

#### AWS CDK Stack Components
```python
class AnyCompanyITPortalStack(Stack):
    """
    Complete infrastructure stack for IT Portal Demo
    """
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        
        # DynamoDB Tables (6 tables)
        self.create_dynamodb_tables()
        
        # Lambda Function
        self.api_lambda = self.create_api_lambda()
        
        # API Gateway
        self.api = self.create_api_gateway()
        
        # S3 + CloudFront
        self.website_bucket = self.create_s3_bucket()
        self.distribution = self.create_cloudfront_distribution()
        
        # Outputs
        self.create_outputs()
```

#### Deployment Configuration
- **Stack Name**: `AnyCompanyITPortalStack`
- **Region**: Configurable (default: us-east-1)
- **Removal Policy**: DESTROY (for easy cleanup)
- **Solution Tracking**: ID `uksb-do9bhieqqh`, tags `(tag:it-portal-demo,operations-automation)`

### Data Flow Architecture

#### User Interaction Flow
```
1. User → CloudFront URL
2. CloudFront → S3 Static Website
3. Browser → Loads HTML + CSS + JavaScript
4. User → Navigates to specific portal
5. JavaScript → Makes API calls (future integration)
6. API Gateway → Routes to Lambda
7. Lambda → Queries DynamoDB
8. Response → Returns through API Gateway
9. JavaScript → Updates portal interface
```

#### AI Automation Flow
```
1. Outlook Email → Mail Polling Service detects "NEW EMPLOYEE ORDER"
2. Email Parser → Extracts employee details and equipment list
3. Onboarding Orchestrator → Coordinates workflow phases
4. Nova Act + AgentCore Browser Tool → Navigates portals:
   a. ITSM Portal → Create ticket, update to In Progress
   b. Inventory Portal → Check stock for each item
   c. Procurement Portal → Create PO for out-of-stock items
   d. Inventory Portal → Add delivered items, allocate to employee
   e. ITSM Portal → Resolve ticket
5. Amazon SES → Send completion notification email
```

### AI Integration Architecture

#### Amazon Nova Act Integration
```python
# Real workflow pattern using Nova Act with AgentCore Browser Tool
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
            # All actions are natural language instructions
            nova.act("Click 'Create Ticket' button")
            nova.act("Fill the form with Title: 'Hardware Request - John Doe'...")
            nova.act("Navigate to inventory portal and search for 'Professional Laptop'")
```

#### AgentCore Browser Tool Configuration
- **Target URLs**: Static HTML portal endpoints
- **Navigation Patterns**: Form filling, button clicking, data extraction
- **Authentication**: None required (demo environment)
- **Error Handling**: Retry logic for network issues
- **Logging**: CloudWatch integration for monitoring

### Security Architecture

#### Demo Environment Security
- **Public Access**: All resources publicly accessible
- **No Authentication**: Simplified for demonstration
- **Mock Data Only**: No sensitive information stored
- **CORS Enabled**: All origins allowed for API access

#### Production Security Recommendations
```
┌─────────────────────────────────────────────────────────────┐
│                Production Security Layer                     │
├─────────────────────────────────────────────────────────────┤
│ • AWS Cognito User Pools (Authentication)                  │
│ • API Gateway Authorizers (Authorization)                  │
│ • S3 Bucket Encryption (Data at Rest)                      │
│ • CloudFront WAF (Web Application Firewall)                │
│ • VPC Endpoints (Private API Access)                       │
│ • CloudTrail Logging (Audit Trail)                         │
│ • Secrets Manager (API Keys)                               │
└─────────────────────────────────────────────────────────────┘
```

### Performance Architecture

#### Expected Performance Characteristics
- **Static Content**: <100ms delivery via CloudFront
- **API Responses**: <500ms for DynamoDB queries
- **Portal Loading**: <2 seconds for complete page load
- **Concurrent Users**: 1-50 (demo environment)
- **Data Volume**: <10MB total across all tables

#### Scaling Considerations
- **DynamoDB**: Auto-scales with pay-per-request pricing
- **Lambda**: Automatic scaling up to 1000 concurrent executions
- **CloudFront**: Global edge locations for content delivery
- **S3**: Virtually unlimited storage and request capacity

### Monitoring and Observability

#### Built-in Monitoring
```
CloudWatch Metrics:
├── Lambda Function
│   ├── Duration, Errors, Invocations
│   └── Memory Utilization
├── DynamoDB Tables
│   ├── Read/Write Capacity
│   ├── Throttled Requests
│   └── Item Count
├── API Gateway
│   ├── Request Count, Latency
│   ├── 4XX/5XX Errors
│   └── Cache Hit/Miss Ratio
└── CloudFront Distribution
    ├── Requests, Data Transfer
    ├── Origin Response Time
    └── Cache Statistics
```

#### Logging Strategy
- **Lambda Logs**: CloudWatch Logs `/aws/lambda/AnyCompanyITPortalStack-APILambda`
- **API Gateway Logs**: Request/response logging (optional)
- **CloudFront Logs**: Access logs to S3 (optional)
- **Application Logs**: Custom logging within portal JavaScript

### Cost Architecture

#### Monthly Cost Breakdown (Demo Usage)
```
Service                 | Usage Pattern           | Monthly Cost
------------------------|-------------------------|-------------
DynamoDB               | <1M requests            | $1-5
Lambda                 | <100K invocations       | $0.20-1
API Gateway            | <10K requests           | $1-3
S3                     | <1GB storage            | $0.50-2
CloudFront             | <10GB transfer          | $0.50-2
------------------------|-------------------------|-------------
Total Estimated Cost   |                         | $3-13
```

#### Cost Optimization Strategies
- **DynamoDB**: Use on-demand pricing for variable workloads
- **Lambda**: Optimize memory allocation for cost/performance balance
- **CloudFront**: Enable compression and caching
- **S3**: Use Intelligent Tiering for infrequently accessed content

### Deployment Architecture

#### Deployment Pipeline
```
1. Source Code (Git Repository)
   ├── static-html-portals/     # Static HTML files
   ├── infrastructure/cdk/      # Infrastructure code
   └── scripts/                 # Deployment scripts

2. Build Process
   ├── Deploy infrastructure      # CDK deployment
   ├── Upload static content      # To S3 bucket
   └── CDK synthesis             # Generate CloudFormation

3. Deploy Infrastructure
   ├── CDK bootstrap           # Prepare AWS account
   ├── CDK deploy              # Create/update stack
   └── Get stack outputs       # Retrieve URLs and IDs

4. Deploy Static Content
   ├── S3 sync                 # Upload HTML/CSS/JS files
   ├── CloudFront invalidation # Clear CDN cache
   └── Populate mock data      # Seed DynamoDB tables
```

#### Environment Management
- **Local Development**: Python HTTP server for testing
- **AWS Development**: Full stack deployment with test data
- **AWS Production**: Production deployment with security hardening

### Integration Points

#### External System Integration
```
┌─────────────────────────────────────────────────────────────┐
│                Integration Architecture                      │
├─────────────────────────────────────────────────────────────┤
│ Email Processing                                            │
│ ├── Microsoft Outlook (Inbox Monitoring)                   │
│ ├── Mail Polling Service (Python)                          │
│ └── Email Parser (Structured Data Extraction)              │
│                                                             │
│ Browser Automation                                          │
│ ├── Amazon Nova Act (Natural Language Instructions)        │
│ ├── AgentCore Browser Tool (Cloud Chrome Browser)          │
│ └── JSON Workflow Definitions (Configurable Actions)       │
│                                                             │
│ Notification System                                         │
│ ├── Amazon SES (Completion Emails)                         │
│ └── CloudWatch Logs (Workflow Monitoring)                   │
│                                                             │
│ Storage                                                     │
│ ├── S3 (Browser Session Recordings)                        │
│ ├── S3 (Nova Act Workflow Step Data)                       │
│ └── DynamoDB (Portal Data)                                 │
│                                                             │
│ Audit and Monitoring                                        │
│ ├── CloudWatch (Lambda and API Metrics)                    │
│ ├── Nova Act Console (Workflow Visualization)              │
│ └── AgentCore Console (Live Browser View)                  │
└─────────────────────────────────────────────────────────────┘
```

#### AI Automation Integration Points
- **Browser Automation**: Nova Act navigates portal URLs with natural language instructions
- **API Integration**: RESTful endpoints for data manipulation (DynamoDB backend)
- **Email Trigger**: Outlook mail-polling service detects "NEW EMPLOYEE ORDER" emails
- **Monitoring**: Nova Act console for workflow visualization, AgentCore console for live browser view
- **Recordings**: S3 bucket stores browser session recordings and workflow step data

## Conclusion

The static HTML architecture provides a robust, scalable, and cost-effective foundation for demonstrating AI-powered legacy system automation. By combining authentic classic computing interfaces with modern AWS infrastructure, the demo effectively showcases how GenAI can bridge the gap between legacy systems and modern automation requirements.

The architecture supports both manual testing and automated AI workflows, making it an ideal platform for demonstrating Amazon Nova Act and AgentCore Browser Tool capabilities in real-world enterprise scenarios.