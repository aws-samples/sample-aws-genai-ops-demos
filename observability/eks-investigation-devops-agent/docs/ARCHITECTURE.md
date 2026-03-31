# DevOps Agent EKS Demo Platform - Architecture

## System Overview

The DevOps Agent EKS Demo Platform is a demo-quality, cloud-native payment processing system built on AWS EKS, demonstrating security, observability, and incident investigation patterns using the Amazon DevOps Agent.

## High-Level Architecture

![Architecture Overview](architecture-overview.drawio.svg)

## Detailed Architecture

![Detailed Architecture](architecture.drawio.svg)

## Component Architecture

### Frontend Layer

**Merchant Portal (React 18 + TypeScript)**
- Single-page application for merchant operations
- Hosted on S3, distributed via CloudFront
- Cognito authentication integration
- Real-time payment status updates

### API Gateway Layer

**Merchant Gateway (Node.js 20 + Express + TypeScript)**
- JWT token validation via Cognito
- Rate limiting (100 req/min per merchant)
- Request routing to backend services
- Correlation ID propagation
- API versioning (/api/v1)

### Business Logic Layer

**Payment Processor (Java 21 + Spring Boot 3.5.x)**
- Core payment processing logic
- Transaction state management
- Database persistence
- X-Ray distributed tracing

**Webhook Service (Node.js 20 + TypeScript)**
- Asynchronous webhook delivery
- Retry logic with exponential backoff
- Delivery status tracking

### Data Layer

**RDS PostgreSQL (Single-AZ)**
- Encrypted at rest (KMS)
- Automated backups
- Connection pooling

### Observability Layer

**Fluent Bit DaemonSet**
- Container log collection
- CloudWatch Logs integration
- Structured logging

**X-Ray Daemon**
- Distributed tracing
- Service map generation
- Performance analysis

### DevOps Agent Integration

**CloudWatch → SNS → Lambda → DevOps Agent**
- Automated incident detection
- Database error monitoring
- HMAC-SHA256 signed webhooks
- Auto-investigation triggers

## Network Architecture

### VPC Design

```
VPC (10.0.0.0/16)
├── Public Subnets (10.0.1.0/24, 10.0.2.0/24)
│   ├── NAT Gateways
│   └── Network Load Balancer
├── Private Subnets (10.0.10.0/24, 10.0.11.0/24)
│   ├── EKS Worker Nodes
│   └── Application Pods
└── Database Subnets (10.0.20.0/24, 10.0.21.0/24)
    └── RDS PostgreSQL (Single-AZ)
```

### Security Groups

| Group | Ingress | Egress |
|-------|---------|--------|
| NLB | 443 from CloudFront | EKS nodes:3000 |
| EKS Nodes | NLB:3000, Node-to-Node | RDS:5432, Internet |
| RDS | EKS nodes:5432 | None |

## Data Flow

### Payment Transaction Flow

```
1. User → CloudFront → S3 (Portal loads)
2. User → CloudFront → NLB → Merchant Gateway (API request)
3. Merchant Gateway → Cognito (JWT validation)
4. Merchant Gateway → Payment Processor (process payment)
5. Payment Processor → RDS (persist transaction)
6. Payment Processor → Webhook Service (trigger webhook)
7. Webhook Service → Merchant endpoint (async delivery)
```

### Logging Flow

```
Container → Fluent Bit → CloudWatch Logs
                              ↓
                        Metric Filter
                              ↓
                        CloudWatch Alarm
                              ↓
                          SNS Topic
                              ↓
                        Lambda Function
                              ↓
                      DevOps Agent Webhook
```

## Security Architecture

### Authentication & Authorization

- **Cognito User Pool**: User authentication
- **JWT Tokens**: API authorization
- **IAM Roles for Service Accounts (IRSA)**: Pod-level permissions

### Encryption

- **In Transit**: TLS 1.2+ everywhere
- **At Rest**: KMS encryption for RDS, S3, Secrets Manager

### Network Security

- **Private Subnets**: Application and database isolation
- **Network Policies**: Pod-to-pod communication control
- **Security Groups**: Layer 4 firewall rules

## Scalability

### Horizontal Scaling

- **EKS Node Group**: Auto-scaling 2-10 nodes
- **Pod Replicas**: HPA based on CPU/memory

### Performance Optimization

- **CloudFront Caching**: Static asset delivery
- **Connection Pooling**: Database connection reuse
- **Rate Limiting**: API abuse prevention

## Disaster Recovery

### Backup Strategy

- **RDS Automated Backups**: Daily snapshots, 7-day retention
- **S3 Versioning**: Frontend asset recovery
- **CloudFormation Templates**: Infrastructure as Code

### High Availability

- **EKS Multi-AZ**: Node distribution across AZs
- **CloudFront**: Global edge distribution
- **Note**: RDS is single-AZ for this demo to reduce cost. For production, enable Multi-AZ for automatic failover.

## Monitoring & Alerting

### Metrics

- **Application**: Request rate, latency, error rate
- **Infrastructure**: CPU, memory, disk, network
- **Business**: Transaction volume, success rate

### Alarms

- **Database Connection Failures**: Triggers DevOps Agent
- **High Error Rate**: SNS notification
- **Resource Exhaustion**: Auto-scaling triggers

## Compliance

### AWS Config Rules

- Encrypted storage validation
- Security group compliance
- IAM policy checks

### Audit Logging

- **CloudTrail**: API call logging
- **VPC Flow Logs**: Network traffic analysis
- **Application Logs**: Business event tracking

## Technology Stack

| Layer | Technology | Version |
|-------|------------|---------|
| Frontend | React + TypeScript + Vite | 18.x |
| API Gateway | Node.js + Express + TypeScript | 20.x |
| Business Logic | Java + Spring Boot | 21.x / 3.5.x |
| Webhooks | Node.js + TypeScript | 20.x |
| Database | PostgreSQL | 15.x |
| Container Orchestration | Amazon EKS | 1.33 |
| IaC | AWS CDK (TypeScript) | 2.x |
| Observability | CloudWatch + Fluent Bit | - |
| Incident Response | Amazon DevOps Agent | - |

## Deployment Architecture

### CDK Stack Hierarchy

All stack IDs include a region suffix for multi-region support (e.g., `DevOpsAgentEksNetwork-us-east-1`).

```
CDK App (cdk/bin/app.ts)
├── DevOpsAgentEksNetwork-${region}    (VPC, Subnets, Security Groups)
├── DevOpsAgentEksAuth-${region}       (Cognito User Pool)
├── DevOpsAgentEksDatabase-${region}   (RDS PostgreSQL) → depends on Network
├── DevOpsAgentEksCompute-${region}    (EKS Cluster, Node Groups) → depends on Network
├── DevOpsAgentEksPipeline-${region}   (CodeBuild, ECR Repositories)
├── DevOpsAgentEksFrontend-${region}   (CloudFront, S3)
├── DevOpsAgentEksMonitoring-${region} (CloudWatch Log Groups, Alarms)
└── DevOpsAgentEksDevOpsAgent-${region} (SNS → Lambda → Webhook) [conditional]
```

## Cost Optimization

### Resource Sizing

- **EKS Nodes**: t4g.medium (Graviton ARM, default) or t3.medium (x86)
- **RDS Instance**: db.t3.micro (Single-AZ)
- **CloudFront**: Pay-per-use

### Cost Monitoring

- **Budget Alerts**: Monthly cost tracking
- **Resource Tagging**: Cost allocation by component

## Future Enhancements

- **Multi-Region Deployment**: Active-active architecture
- **Service Mesh**: Istio for advanced traffic management
- **GitOps**: ArgoCD for continuous deployment
- **Chaos Engineering**: AWS FIS integration

## Contributing

We welcome community contributions! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
