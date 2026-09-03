# Storylane Script: Intelligent Aurora MySQL Incident Investigation with Amazon DevOps Agent

## Demo Overview
- **Total slides**: 13
- **Duration**: ~15-20 minutes self-guided
- **Scenario used**: connection-storm (writer connection exhaustion)
- **Flow**: Problem → Automated Detection → Agent Investigation → Business Context → Resolution

---

## Slide 1: Title / Hook
**Screen**: Custom intro slide or architecture diagram
**Script**: "Your production database starts refusing connections at 2 AM. Instead of your on-call DBRE spending 45 minutes in Performance Insights and error logs, Amazon DevOps Agent investigates automatically, finds the root cause, and tells you exactly who it's affecting and what it's costing — in minutes."
**Callout**: "6 real-world Aurora failure scenarios | Automated root-cause analysis | Business context via MCP"

---

## Slide 2: Architecture Overview
**Screen**: Architecture diagram (see ARCHITECTURE.md)
**Script**: "The demo deploys a self-contained Aurora MySQL cluster — a writer and a reader in isolated subnets — plus a bastion load-generator. CloudWatch alarms watch connections, CPU, deadlocks, memory, and replica lag; an EventBridge rule catches failover events. When something fires, a Lambda webhook wakes DevOps Agent, which investigates using metrics, Performance Insights, and logs, and calls an MCP server for business context."
**Callout**: Highlight the flow: CloudWatch Alarm → SNS → Lambda → DevOps Agent → MCP Server

---

## Slide 3: Aurora Cluster Healthy (Before)
**Screen**: RDS Console → Databases → `aurora-demo-cluster` → writer + reader both Available
**Script**: "Both the writer and the reader are healthy and Available. This is steady state — a normal OLTP e-commerce workload."
**Callout**: Point to writer (`aurora-demo-writer`) and reader (`aurora-demo-reader`) in green

---

## Slide 4: CloudWatch Alarms — All OK (Before)
**Screen**: CloudWatch Console → Alarms → filter "aurora-demo"
**Script**: "Six CloudWatch alarms monitor this cluster — connections, CPU, deadlocks, plus dedicated memory-pressure and replica-lag alarms. All currently OK."
**Callout**: Highlight the alarms in the green/OK state

---

## Slide 5: Inject Failure — Connection Storm
**Screen**: Terminal showing the inject command
**Script**: "A very common real-world incident: a bad application deploy leaks connections, or a retry storm floods the writer. We simulate it with one command — 200 held connections against the writer."
**Callout**: Highlight `bash scripts/inject-failure.sh connection-storm --key-file ~/.ssh/your-key.pem`
**Also show**: "connection-storm active (200 connections). DatabaseConnections will climb."

---

## Slide 6: CloudWatch Alarm Fires
**Screen**: CloudWatch Console → `aurora-demo-connections-high` in ALARM (red)
**Script**: "Within a minute or two, the DatabaseConnections alarm crosses its threshold and transitions to ALARM. That fires SNS → Lambda → the DevOps Agent webhook."
**Callout**: Point to the red ALARM state and the DatabaseConnections graph climbing

---

## Slide 7: DevOps Agent — Investigation Received
**Screen**: Operator App → Investigation list showing the new investigation
**Script**: "DevOps Agent receives the webhook and opens an investigation on its own. No human triggered this — the agent woke up by itself."
**Callout**: Point to the new investigation title and timestamp

---

## Slide 8: DevOps Agent — Reading Metrics & Performance Insights
**Screen**: Operator App → inside the investigation → the agent's steps
**Script**: "The agent pulls DatabaseConnections and correlates with Performance Insights and the Aurora error log. It sees connections pinned near the instance limit while CPU stays moderate — the signature of a connection storm, not a query-cost problem."
**Callout**: Highlight the connection-count observation and the agent's reasoning

---

## Slide 9: DevOps Agent — MCP Query (Service Dependencies)
**Screen**: Operator App → agent calling `get_service_dependencies`
**Script**: "The agent asks the MCP server who depends on this database. It learns checkout-service (CRITICAL, read-write), order-history-api (HIGH), and analytics-etl all rely on the cluster — roughly 18,000 active shopper sessions affected."
**Callout**: Highlight the dependent services and criticality levels

---

## Slide 10: DevOps Agent — MCP Query (Cost & Compliance)
**Screen**: Operator App → agent calling `get_cost_impact` and `get_compliance_status`
**Script**: "It quantifies the impact — about $5,100 per minute in blocked checkout revenue, ~610 orders/minute — and flags PCI-DSS (15-min reporting) and GDPR obligations because the cluster holds payment tokens and customer PII."
**Callout**: Highlight "$5,100/min" and "PCI-DSS: 15 min reporting"

---

## Slide 11: DevOps Agent — Final Incident Report
**Screen**: Operator App → the agent's summary / conclusion
**Script**: "The agent produces a complete report: root cause is writer connection exhaustion, likely a pool leak from a recent deploy, with quantified business impact and compliance deadlines. Your DBRE reads a conclusion, not a pile of logs."
**Callout**: Highlight root cause + recommended action (raise a fix, restart pool, review recent deploy)

---

## Slide 12: On-Demand Chat — Follow-up
**Screen**: Operator App → Chat panel
**Script**: "The agent isn't one-shot. Your engineer can ask follow-ups and get specific remediation steps."
**Prompt to type**: "What are the exact steps to relieve the connection storm and prevent recurrence?"
**Callout**: Highlight the agent's remediation steps (kill idle sessions, cap the pool, add a connection alarm)

---

## Slide 13: CTA — Try It Yourself
**Screen**: Custom closing slide
**Script**: "This is one of six Aurora scenarios — including CPU spikes, deadlocks, failover, memory pressure, and replica lag. Deploy it in your account and show your customers how DevOps Agent turns 2 AM database incidents into automated investigations."
**Links to show**:
- GitHub: https://github.com/aws-samples/sample-aws-genai-ops-demos
- Demo Site: https://aws-samples.github.io/sample-aws-genai-ops-demos/
**Callout**: "6 scenarios | ~25 min setup | Aurora writer+reader | Full cleanup script included"

---

## Capture Tips
- Use a clean browser; AWS console in light mode reads better in Storylane.
- For the RDS console, crop to the cluster + its two instances.
- For the Operator App, wait until the agent finishes each step before capturing.
- If the agent output is long, capture the most impactful section (root cause + business impact).

## Timing Notes
- After injecting: wait ~1-3 min for the connections alarm to fire.
- Agent investigation: ~2-5 min to complete including MCP calls.
- Total capture time: ~15-20 min for all screenshots.
- Remember to roll back after capture: `bash scripts/inject-failure.sh connection-storm --key-file <key> --rollback`
