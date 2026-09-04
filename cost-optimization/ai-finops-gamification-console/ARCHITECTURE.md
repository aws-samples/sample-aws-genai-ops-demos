# AI FinOps Gamification Console - Architecture

## System Overview

The FinOps Gamification Console is a web application that adds ownership, accountability, and gamification capabilities on top of AWS FinOps Agent. It implements a human-in-the-loop workflow where findings must be manually accepted before any action, with a learning loop that suppresses noise over time.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AWS FinOps Agent (External)                         │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Scheduled Automation (Weekly)                                       │   │
│  │  • Prompt: "Generate cost report: top 10 services, anomalies >10%,  │   │
│  │    patterns, optimization recommendations"                           │   │
│  │  • Output: HTML artifact + Slack post                                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                                  │ Posts to Slack channel with HTML attachment
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Ingestion Layer                                 │
│                                                                             │
│  ┌─────────────────┐    ┌──────────────────────┐    ┌─────────────────┐   │
│  │   EventBridge   │───▶│   Ingestion Lambda   │───▶│    DynamoDB     │   │
│  │   (Scheduled)   │    │                      │    │   (findings)    │   │
│  │   rate(1 hour)  │    │  • Slack API client  │    │                 │   │
│  └─────────────────┘    │  • HTML parser       │    │  Status: NEW    │   │
│                         │  • Finding normalizer │    └─────────────────┘   │
│                         └──────────────────────┘                            │
│                                                                             │
│  Retrieval Adapters (pluggable):                                           │
│  ├── SlackAdapter (default): channels.history + files.info                 │
│  └── NativeAdapter (future): finops.GetArtifactContent                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  │ Findings written to backlog
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Presentation Layer                                │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    CloudFront Distribution                           │   │
│  │                         (HTTPS, caching)                             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                  │                                          │
│                                  ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         S3 Bucket                                    │   │
│  │                    (React + Cloudscape SPA)                          │   │
│  │                                                                      │   │
│  │  Pages:                                                              │   │
│  │  ├── /dashboard    - Overview, pending counts, team scores          │   │
│  │  ├── /backlog      - Findings list, accept/reject workflow          │   │
│  │  ├── /teams        - Team management, champion assignment           │   │
│  │  ├── /scoping      - Service/account/tag → team mapping             │   │
│  │  ├── /leaderboard  - Rankings, badges, streaks                      │   │
│  │  └── /learnings    - Rejected finding patterns                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  │ API calls (authenticated)
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Authentication Layer                               │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      Amazon Cognito User Pool                        │   │
│  │                                                                      │   │
│  │  Groups (RBAC):                                                      │   │
│  │  ├── finops-admin  → Full access: all teams, config, governance     │   │
│  │  ├── champion      → Own team's findings, accept/reject, stats      │   │
│  │  └── viewer        → Read-only dashboards and leaderboard           │   │
│  │                                                                      │   │
│  │  Features: MFA optional, password policies, JWT tokens              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  │ JWT token in Authorization header
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              API Layer                                       │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        API Gateway (REST)                            │   │
│  │                                                                      │   │
│  │  Endpoints:                                                          │   │
│  │  ├── GET    /findings              List findings (filtered)         │   │
│  │  ├── GET    /findings/{id}         Get finding detail               │   │
│  │  ├── POST   /findings/{id}/accept  Accept finding                   │   │
│  │  ├── POST   /findings/{id}/reject  Reject finding (+ reason)        │   │
│  │  ├── PATCH  /findings/{id}/status  Update status (state machine)    │   │
│  │  ├── GET    /teams                 List teams                       │   │
│  │  ├── POST   /teams                 Create team                      │   │
│  │  ├── GET    /scoping               List scoping rules               │   │
│  │  ├── POST   /scoping               Create scoping rule              │   │
│  │  ├── GET    /leaderboard           Get leaderboard                  │   │
│  │  ├── GET    /scores/{userId}       Get user scores                  │   │
│  │  └── GET    /learnings             List learning records            │   │
│  │                                                                      │   │
│  │  Authorization: Cognito User Pool Authorizer                        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                  │                                          │
│                                  ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         Lambda Functions                             │   │
│  │                                                                      │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐     │   │
│  │  │ findings_handler │  │  teams_handler  │  │ scoring_engine  │     │   │
│  │  │                 │  │                 │  │                 │     │   │
│  │  │ • CRUD ops      │  │ • Team CRUD     │  │ • Calculate     │     │   │
│  │  │ • State machine │  │ • Champion mgmt │  │   scores        │     │   │
│  │  │ • Accept/reject │  │ • Scoping rules │  │ • Award badges  │     │   │
│  │  │ • Learning loop │  │                 │  │ • Update streak │     │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Data Layer                                      │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         DynamoDB Tables                              │   │
│  │                                                                      │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │   │
│  │  │   teams     │  │   scoping   │  │  findings   │                 │   │
│  │  │             │  │             │  │             │                 │   │
│  │  │ PK: teamId  │  │ PK: ruleId  │  │ PK: findingId│                │   │
│  │  │             │  │             │  │ GSI1: status │                │   │
│  │  │ name        │  │ teamId      │  │ GSI2: teamId │                │   │
│  │  │ champions[] │  │ type (svc/  │  │             │                 │   │
│  │  │ createdAt   │  │  acct/tag)  │  │ status      │                 │   │
│  │  │             │  │ pattern     │  │ assignedTo  │                 │   │
│  │  └─────────────┘  └─────────────┘  │ savingsUsd  │                 │   │
│  │                                    │ createdAt   │                 │   │
│  │  ┌─────────────┐  ┌─────────────┐  │ resolvedAt  │                 │   │
│  │  │  learnings  │  │   scores    │  └─────────────┘                 │   │
│  │  │             │  │             │                                   │   │
│  │  │ PK: learnId │  │ PK: usrId   │                                   │   │
│  │  │             │  │ SK: month   │                                   │   │
│  │  │ service     │  │             │                                   │   │
│  │  │ category    │  │ accepted    │                                   │   │
│  │  │ pattern     │  │ resolved    │                                   │   │
│  │  │ reason      │  │ avgRespHrs  │                                   │   │
│  │  │ confidence  │  │ savingsUsd  │                                   │   │
│  │  │ matchCount  │  │ badges[]    │                                   │   │
│  │  └─────────────┘  │ streak      │                                   │   │
│  │                   └─────────────┘                                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                       Secrets Manager                                │   │
│  │                                                                      │   │
│  │  • SlackBotToken: Bot token for Slack API access                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Models

### Finding Entity
```typescript
interface Finding {
  findingId: string;           // UUID
  source: 'finops-agent';      // Source system
  sourceId: string;            // Original ID from source
  
  // Content
  title: string;
  description: string;
  category: 'anomaly' | 'optimization' | 'top-spender';
  severity: 'low' | 'medium' | 'high' | 'critical';
  
  // Cost data
  currentCostUsd: number;
  projectedSavingsUsd: number;
  actualSavingsUsd?: number;   // Set when resolved
  
  // Affected resources
  service: string;             // e.g., 'AmazonEC2'
  accountId?: string;
  resourceArn?: string;
  tags?: Record<string, string>;
  
  // Lifecycle state
  status: FindingStatus;
  assignedTeamId?: string;
  assignedChampionId?: string;
  
  // Timestamps
  detectedAt: string;          // ISO 8601
  ingestedAt: string;
  acceptedAt?: string;
  resolvedAt?: string;
  
  // Learning
  learningAnnotation?: string; // "Previously rejected: ..."
  learningConfidence?: number; // 0-1 match confidence
}

type FindingStatus = 
  | 'NEW'
  | 'PENDING_ACCEPTANCE'
  | 'ACCEPTED'
  | 'ASSIGNED'
  | 'IN_PROGRESS'
  | 'RESOLVED'
  | 'CLOSED'
  | 'REJECTED';
```

### Team Entity
```typescript
interface Team {
  teamId: string;              // UUID
  name: string;
  description?: string;
  
  champions: Champion[];
  
  createdAt: string;
  updatedAt: string;
}

interface Champion {
  userId: string;              // Cognito sub
  email: string;
  name: string;
  role: 'lead' | 'member';
}
```

### Scoping Rule Entity
```typescript
interface ScopingRule {
  ruleId: string;              // UUID
  teamId: string;
  
  type: 'service' | 'account' | 'tag';
  pattern: string;             // e.g., 'AmazonEC2', '123456789012', 'env:prod'
  
  priority: number;            // Lower = higher priority
  
  createdAt: string;
  createdBy: string;
}
```

### Learning Record Entity
```typescript
interface LearningRecord {
  learningId: string;          // UUID
  
  // Match criteria
  service?: string;
  category?: string;
  tagPattern?: string;
  
  // Rejection context
  reason: string;
  rejectedBy: string;
  rejectedAt: string;
  
  // Effectiveness
  matchCount: number;          // Times this pattern matched
  confidence: number;          // 0-1, increases with matches
  
  active: boolean;
}
```

### Score Entity
```typescript
interface Score {
  userId: string;              // Cognito sub
  month: string;               // YYYY-MM
  
  // Metrics
  findingsAssigned: number;
  findingsAccepted: number;
  findingsResolved: number;
  avgResponseHours: number;
  totalSavingsUsd: number;
  
  // Gamification
  badges: Badge[];
  streak: number;              // Consecutive months with all resolved
  rank?: number;               // Calculated dynamically
}

type Badge = 
  | 'speed_demon'              // Fastest avg response time
  | 'cost_killer_bronze'       // $1k+ saved
  | 'cost_killer_silver'       // $10k+ saved
  | 'cost_killer_gold'         // $100k+ saved
  | 'clean_slate'              // Zero open findings at month end
  | 'streak_3'                 // 3+ month streak
  | 'streak_6'                 // 6+ month streak
  | 'streak_12';               // 12+ month streak
```

## State Machine: Finding Lifecycle

```
                    ┌─────────────────────────────────────────────┐
                    │                                             │
                    ▼                                             │
┌───────┐    ┌──────────────────┐    ┌──────────┐               │
│  NEW  │───▶│ PENDING_ACCEPTANCE│───▶│ ACCEPTED │               │
└───────┘    └──────────────────┘    └──────────┘               │
                    │                      │                     │
                    │ reject()             │ assign()            │
                    ▼                      ▼                     │
             ┌──────────┐           ┌──────────┐                │
             │ REJECTED │           │ ASSIGNED │                │
             └──────────┘           └──────────┘                │
                    │                      │                     │
                    │                      │ start()             │
                    │                      ▼                     │
                    │               ┌─────────────┐              │
                    │               │ IN_PROGRESS │              │
                    │               └─────────────┘              │
                    │                      │                     │
                    │                      │ resolve(savings)    │
                    │                      ▼                     │
                    │               ┌──────────┐                │
                    │               │ RESOLVED │                │
                    │               └──────────┘                │
                    │                      │                     │
                    │                      │ close()             │
                    │                      ▼                     │
                    │               ┌──────────┐                │
                    └──────────────▶│  CLOSED  │◀───────────────┘
                                    └──────────┘
```

### Transition Rules
| From | To | Action | Requirements |
|------|----|--------|--------------|
| NEW | PENDING_ACCEPTANCE | Auto (ingestion) | Finding parsed successfully |
| PENDING_ACCEPTANCE | ACCEPTED | accept() | Admin or champion role |
| PENDING_ACCEPTANCE | REJECTED | reject(reason) | Admin or champion role, reason required |
| ACCEPTED | ASSIGNED | assign(team, champion) | Team and champion specified |
| ASSIGNED | IN_PROGRESS | start() | Champion acknowledges |
| IN_PROGRESS | RESOLVED | resolve(savings) | Actual savings recorded |
| RESOLVED | CLOSED | close() | Admin review complete |
| REJECTED | CLOSED | Auto after learning | Learning record created |

## Learning Loop Algorithm

```python
def process_rejection(finding: Finding, reason: str, user_id: str) -> LearningRecord:
    """
    When a finding is rejected, create or update a learning record
    that will annotate or suppress similar future findings.
    """
    
    # 1. Create learning signature from finding attributes
    signature = create_signature(
        service=finding.service,
        category=finding.category,
        tag_pattern=extract_tag_pattern(finding.tags)
    )
    
    # 2. Check for existing learning record with similar signature
    existing = find_similar_learning(signature)
    
    if existing:
        # 3a. Update existing record
        existing.match_count += 1
        existing.confidence = min(0.95, existing.confidence + 0.1)
        return update_learning(existing)
    else:
        # 3b. Create new learning record
        return create_learning(
            service=finding.service,
            category=finding.category,
            tag_pattern=signature.tag_pattern,
            reason=reason,
            rejected_by=user_id,
            confidence=0.3  # Initial confidence
        )

def annotate_new_finding(finding: Finding) -> Finding:
    """
    When a new finding is ingested, check if it matches any
    learning records and annotate accordingly.
    """
    
    matching_learnings = find_matching_learnings(
        service=finding.service,
        category=finding.category,
        tags=finding.tags
    )
    
    if not matching_learnings:
        return finding
    
    best_match = max(matching_learnings, key=lambda l: l.confidence)
    
    if best_match.confidence >= SUPPRESSION_THRESHOLD:  # e.g., 0.8
        # High confidence: auto-suppress
        finding.status = 'REJECTED'
        finding.learning_annotation = f"Auto-suppressed: {best_match.reason}"
    else:
        # Lower confidence: annotate but don't suppress
        finding.learning_annotation = f"Previously rejected: {best_match.reason}"
        finding.learning_confidence = best_match.confidence
    
    # Update match count
    best_match.match_count += 1
    update_learning(best_match)
    
    return finding
```

## Scoring Engine

```python
def calculate_monthly_scores(month: str) -> List[Score]:
    """
    Calculate scores for all champions for a given month.
    Called at month end or on-demand for leaderboard.
    """
    
    scores = []
    
    for champion in get_all_champions():
        findings = get_findings_for_champion(champion.user_id, month)
        
        assigned = len([f for f in findings if f.assigned_at])
        accepted = len([f for f in findings if f.accepted_at])
        resolved = len([f for f in findings if f.resolved_at])
        
        # Calculate average response time (accept to resolve)
        response_times = [
            (f.resolved_at - f.accepted_at).hours
            for f in findings
            if f.resolved_at and f.accepted_at
        ]
        avg_response = mean(response_times) if response_times else 0
        
        # Calculate total savings
        total_savings = sum(f.actual_savings_usd or 0 for f in findings)
        
        # Calculate badges
        badges = calculate_badges(
            avg_response=avg_response,
            total_savings=total_savings,
            open_findings=assigned - resolved,
            current_badges=get_current_badges(champion.user_id)
        )
        
        # Calculate streak
        previous_score = get_previous_score(champion.user_id, month)
        if resolved == assigned and assigned > 0:
            streak = (previous_score.streak if previous_score else 0) + 1
        else:
            streak = 0
        
        scores.append(Score(
            user_id=champion.user_id,
            month=month,
            findings_assigned=assigned,
            findings_accepted=accepted,
            findings_resolved=resolved,
            avg_response_hours=avg_response,
            total_savings_usd=total_savings,
            badges=badges,
            streak=streak
        ))
    
    # Calculate ranks
    scores.sort(key=lambda s: (s.total_savings_usd, -s.avg_response_hours), reverse=True)
    for i, score in enumerate(scores):
        score.rank = i + 1
    
    return scores

def calculate_badges(avg_response, total_savings, open_findings, current_badges):
    """Award badges based on performance thresholds."""
    badges = list(current_badges)
    
    # Speed Demon: fastest 10% response time
    if avg_response < SPEED_THRESHOLD:
        badges.append('speed_demon')
    
    # Cost Killer tiers
    if total_savings >= 100000:
        badges.append('cost_killer_gold')
    elif total_savings >= 10000:
        badges.append('cost_killer_silver')
    elif total_savings >= 1000:
        badges.append('cost_killer_bronze')
    
    # Clean Slate: no open findings
    if open_findings == 0:
        badges.append('clean_slate')
    
    return list(set(badges))
```

## Security Architecture

### Authentication Flow
```
┌─────────┐         ┌─────────────┐         ┌─────────────┐
│ Browser │────────▶│   Cognito   │────────▶│ User Pool   │
│         │◀────────│  Hosted UI  │◀────────│             │
└─────────┘  JWT    └─────────────┘  Tokens └─────────────┘
     │                                              │
     │ Authorization: Bearer <token>                │
     ▼                                              │
┌─────────────┐         ┌─────────────────┐        │
│ API Gateway │────────▶│ Cognito         │◀───────┘
│             │         │ Authorizer      │
└─────────────┘         └─────────────────┘
     │
     │ claims: { sub, email, cognito:groups }
     ▼
┌─────────────┐
│   Lambda    │ Check cognito:groups for RBAC
└─────────────┘
```

### Authorization Matrix
| Resource | finops-admin | champion | viewer |
|----------|:------------:|:--------:|:------:|
| GET /findings | All | Own team | All (read) |
| POST /findings/{id}/accept | Y | Own team | - |
| POST /findings/{id}/reject | Y | Own team | - |
| GET /teams | Y | Own team | Y |
| POST /teams | Y | - | - |
| GET /scoping | Y | Own team | Y |
| POST /scoping | Y | - | - |
| GET /leaderboard | Y | Y | Y |
| GET /learnings | Y | Y | Y |

## Deployment Architecture

### CDK Stack Structure
```
FinOpsGamificationConsole-${region}
├── AuthConstruct
│   ├── Cognito User Pool
│   ├── User Pool Client
│   └── Groups (finops-admin, champion, viewer)
│
├── DataConstruct
│   ├── DynamoDB: teams
│   ├── DynamoDB: scoping
│   ├── DynamoDB: findings (+ GSIs)
│   ├── DynamoDB: learnings
│   ├── DynamoDB: scores
│   └── Secrets Manager: SlackBotToken
│
├── ApiConstruct
│   ├── Lambda: findings_handler
│   ├── Lambda: teams_handler
│   ├── Lambda: scoring_engine
│   ├── API Gateway REST API
│   └── Cognito Authorizer
│
├── IngestionConstruct
│   ├── Lambda: ingestion_handler
│   └── EventBridge Rule (scheduled)
│
└── FrontendConstruct
    ├── S3 Bucket (website)
    ├── CloudFront Distribution
    └── Origin Access Control
```

### Multi-Region Considerations
- **Stack ID includes region**: `FinOpsGamificationConsole-${region}`
- **DynamoDB**: Single-region by default (demo); for production, consider Global Tables
- **CloudFront**: Global edge locations, origin in deployment region
- **Cognito**: Regional service; deploy in same region as API
- **FinOps Agent constraint**: Preview limited to us-east-1; ingestion Lambda can reach cross-region

## Performance Considerations

### DynamoDB Access Patterns
| Access Pattern | Table | Key/Index |
|----------------|-------|-----------|
| Get finding by ID | findings | PK: findingId |
| List findings by status | findings | GSI1: status-createdAt |
| List findings by team | findings | GSI2: teamId-status |
| Get team by ID | teams | PK: teamId |
| List scoping rules | scoping | Scan (small table) |
| Get user scores | scores | PK: userId, SK: month |
| Leaderboard query | scores | GSI: month-totalSavingsUsd |

### Caching Strategy
- **CloudFront**: Cache static assets (365 days), API responses (TTL based on endpoint)
- **Lambda**: Warm start optimization for frequent endpoints
- **Client-side**: React Query for API response caching

## Monitoring and Observability

### CloudWatch Metrics
- Lambda invocations, errors, duration
- API Gateway latency, 4xx/5xx rates
- DynamoDB read/write capacity units
- Cognito sign-in success/failure

### Custom Metrics
- Findings ingested per hour
- Accept/reject ratio
- Average time to resolution
- Learning effectiveness (suppress rate)

### Alarms
- API error rate > 5%
- Ingestion failures
- DynamoDB throttling
- Cognito authentication failures
