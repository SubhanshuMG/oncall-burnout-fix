# oncall-burnout-fix

> **Companion code for:** [The 3AM Problem: Why On-Call Burnout Is a System Design Failure, Not a People Problem](https://github.com/SubhanshuMG/oncall-burnout-fix)

Automated on-call incident triage system that groups, diagnoses, and remediates production incidents before they reach a human at 3AM.

---

## What This Does

Transforms this:

```
Mon 02:14 — ALERT: payment-processor CPU > 90%
Mon 02:41 — ALERT: order-service DB connection pool exhausted
Tue 03:07 — ALERT: kafka consumer lag > 50,000 messages
Wed 01:22 — ALERT: payment-processor CPU > 90%
... 27 more alerts ...
```

Into this:

```
[GROUPED ALERT — 1 page]:
  Root cause: order-service DB connection pool exhaustion
  Affected: payment, checkout, inventory, order, notification (6 services)
  Confidence: 94%
  Action: [Approve Auto-Fix] [Run Manually] [Escalate]
```

---

## Architecture

```
Production Systems
      │ metrics + logs + traces
      ▼
Observability Layer (Prometheus + Loki + Grafana)
      │ raw alerts
      ▼
Alert Intelligence Layer (Alertmanager)
  - Deduplication
  - Grouping by causality
  - Inhibition (suppress child alerts)
      │ grouped alert bundle
      ▼
Automated Diagnosis Engine (Python / FastAPI)
  - Dependency graph traversal
  - Metric correlation (Pearson r)
  - Historical pattern matching (Redis)
      │ diagnosis context
      ▼
AIOps Triage Layer (LLM — GPT-4 / Claude)
  - Natural language incident summary
  - Runbook selection
  - Confidence-scored remediation plan
      │ triage package
      ▼
Runbook Automation Layer (Ansible)
  - Parameterized playbooks
  - Human-in-the-loop approval gate
  - Full audit trail + rollback
      │ single Slack notification
      ▼
Engineer Interface
  [Approve Auto-Fix] [Run Manually] [Escalate]
```

---

## Repo Structure

```
oncall-burnout-fix/
├── src/
│   ├── __init__.py
│   ├── models.py               # Pydantic data models (Alert, AlertBundle, DiagnosisResult)
│   ├── graph.py                # Service dependency graph (NetworkX)
│   ├── correlator.py           # Prometheus metric correlation
│   ├── diagnosis.py            # Root cause analysis engine
│   ├── history.py              # Redis-backed incident pattern store
│   ├── aiops.py                # LLM triage + Slack notification
│   └── runbook_handler.py      # Slack action handler + Ansible executor
├── tests/
│   ├── test_diagnosis.py       # Unit tests: graph, engine, confidence scoring
│   ├── test_integration.py     # End-to-end: full pipeline simulation
│   └── fixtures/
│       └── sample_alerts.json  # Black Friday payment cascade scenario
├── playbooks/
│   ├── db-connection-pool-fix.yml    # RB-DB-001: Scale DB connection pool
│   ├── app-restart-graceful.yml      # RB-APP-001: Graceful app restart
│   ├── kafka-consumer-reset.yml      # RB-KAFKA-001: Reset consumer group
│   └── app-scale-horizontal.yml      # RB-APP-002: Horizontal pod scaling
├── k8s/
│   ├── deployment.yaml               # Deployment + Service + RBAC
│   └── prometheus-alert-rules.yaml   # PrometheusRule CRD with semantic labels
├── alertmanager-config.yaml          # Alertmanager: grouping + inhibition + routing
├── main.py                           # FastAPI app entrypoint
├── requirements.txt
├── Dockerfile
└── docker-compose.yml                # Local dev stack
```

---

## Quick Start

### Prerequisites

- Python 3.12+
- Docker + Docker Compose
- Kubernetes cluster with Prometheus stack (for production)
- OpenAI API key (or swap in any LLM)
- Slack webhook URL

### Local Development

```bash
git clone https://github.com/SubhanshuMG/oncall-burnout-fix.git
cd oncall-burnout-fix

# Set environment variables
cp .env.example .env
# Edit .env with your SLACK_WEBHOOK_URL and OPENAI_API_KEY

# Start the full local stack (Prometheus, Redis, diagnosis engine)
docker-compose up -d

# Run unit tests
pip install -r requirements.txt
pytest tests/test_diagnosis.py -v

# Run integration tests
pytest tests/test_integration.py -v --asyncio-mode=auto

# Coverage report
pytest tests/ --cov=src --cov-report=html
```

### Deploy to Kubernetes

```bash
# Install Prometheus stack
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace

# Apply alert rules
kubectl apply -f k8s/prometheus-alert-rules.yaml

# Apply Alertmanager config (update slack URL first)
kubectl apply -f alertmanager-config.yaml

# Create secrets
kubectl create secret generic oncall-secrets \
  --from-literal=slack-webhook-url=YOUR_URL \
  --from-literal=openai-api-key=YOUR_KEY \
  -n monitoring

# Deploy diagnosis engine
kubectl apply -f k8s/deployment.yaml
```

---

## The Four Layers

### Layer 1: Alert Intelligence (Alertmanager)
`alertmanager-config.yaml` + `k8s/prometheus-alert-rules.yaml`

Every alert carries semantic labels (`layer`, `impact_type`, `root_cause_candidate`). Alertmanager uses these to:
- **Group** related alerts into a single incident bundle
- **Inhibit** downstream symptom alerts when a root cause alert fires
- **Route** by severity and layer with appropriate `group_wait` windows

### Layer 2: Diagnosis Engine (`src/diagnosis.py`)
Receives the grouped alert bundle via webhook and runs:
1. **Label scan** — explicit `root_cause_candidate=true` alerts win immediately
2. **Graph traversal** — `ServiceDependencyGraph` finds the most-depended-on affected node
3. **Metric correlation** — Pearson r between DB connections and error rates via Prometheus API
4. **Pattern matching** — Redis lookup of fingerprinted past incidents
5. **Confidence scoring** — weighted combination of all signals

### Layer 3: AIOps Triage (`src/aiops.py`)
Sends the structured diagnosis to an LLM with a strict prompt and `temperature=0.1`. Returns a JSON triage package with `one_line_summary`, `immediate_action`, `steps`, `do_not_do`, and `escalate_if`. Falls back gracefully if LLM is unavailable.

### Layer 4: Runbook Automation (`playbooks/`, `src/runbook_handler.py`)
Ansible playbooks parameterized with `incident_id`, `triggered_by`, `runbook_id`. Human approval required via Slack button before execution. Full audit trail via Kubernetes annotations on patched resources.

---

## Key Design Decisions

| Decision | Why |
|---|---|
| `root_cause_candidate` label on alert rules | Explicit signal beats graph heuristics; engineers encode domain knowledge at authorship time |
| Pearson correlation to validate hypothesis | Prevents false confidence; if DB metrics don't correlate with errors, score drops |
| Redis for pattern history (not a DB) | 90-day TTL, fast lookup, low ops overhead for incident fingerprints |
| LLM temperature = 0.1 | Deterministic, factual output for 3AM reading; not the place for creativity |
| Ansible `--diff` flag | Every auto-remediation shows exactly what changed; essential for postmortem |
| Human-in-the-loop for auto-fix | Automation should earn trust incrementally; approval gate is the right default |

---

## Metrics to Track

| Metric | Target |
|---|---|
| Pages per rotation week | ↓ 70% |
| Time to diagnose (MTTD) | < 5 minutes |
| False positive rate | < 10% |
| Auto-resolution rate | > 40% of P2s |
| Alert deduplication ratio | > 10:1 |
| Engineer rotation health score | > 7/10 |

---

## Customizing the Dependency Graph

Edit `src/graph.py` → `_build_default_graph()` or point it at your service catalog:

```python
graph.load_from_file("service-catalog.json")
```

Format:
```json
{
  "nodes": ["payment-service", "order-service", "postgres-rds"],
  "edges": [
    {"from": "payment-service", "to": "order-service"},
    {"from": "order-service", "to": "postgres-rds"}
  ]
}
```

---

## License

MIT. Use it, adapt it, fix your on-call rotation.

---

*Built as companion code for the article [The 3AM Problem](https://github.com/SubhanshuMG/oncall-burnout-fix). If this helped, share it with whoever controls your monitoring budget.*
