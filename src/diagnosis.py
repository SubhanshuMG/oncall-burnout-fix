# src/diagnosis.py
# https://github.com/SubhanshuMG/oncall-burnout-fix
import uuid
import math
import structlog
from datetime import datetime
from typing import Optional

from .models import AlertBundle, DiagnosisResult, AlertSeverity
from .graph import ServiceDependencyGraph
from .correlator import MetricCorrelator
from .history import IncidentHistory

log = structlog.get_logger()


class DiagnosisEngine:
    """
    Orchestrates root cause analysis for an alert bundle.

    Pipeline:
      1. Extract affected services from alert bundle
      2. Identify root cause candidates (root_cause_candidate=true labels)
      3. Traverse dependency graph to find cascade origin
      4. Validate with metric correlation (Pearson r)
      5. Match against historical patterns (Redis)
      6. Compute weighted confidence score
      7. Select runbook and build human-readable description
    """

    def __init__(self):
        self.graph = ServiceDependencyGraph()
        self.correlator = MetricCorrelator()
        self.history = IncidentHistory()

    async def diagnose(self, bundle: AlertBundle) -> DiagnosisResult:
        incident_id = str(uuid.uuid4())[:8]
        firing_alerts = [a for a in bundle.alerts if a.status == "firing"]
        incident_start = min(a.starts_at for a in firing_alerts)

        log.info(
            "diagnosis_started",
            incident_id=incident_id,
            alert_count=len(firing_alerts),
            namespace=bundle.common_labels.get("namespace", "unknown"),
        )

        # Step 1: Extract all affected services
        affected_services = list(set(
            a.labels.service
            for a in firing_alerts
            if a.labels.service
        ))

        # Step 2: Find explicit root cause candidates from alert labels
        root_cause_candidates = [
            a for a in firing_alerts
            if a.labels.root_cause_candidate and a.labels.service
        ]

        # Step 3: Use dependency graph if no explicit candidates
        if root_cause_candidates:
            root_cause_service = root_cause_candidates[0].labels.service
            root_cause_alert = root_cause_candidates[0].labels.alertname
            method = "label_candidate"
        else:
            root_cause_service = self.graph.find_likely_root_cause(affected_services)
            root_cause_alert = next(
                (a.labels.alertname for a in firing_alerts
                 if a.labels.service == root_cause_service),
                None
            )
            method = "graph_traversal"

        log.info(
            "root_cause_identified",
            incident_id=incident_id,
            root_cause_service=root_cause_service,
            method=method,
        )

        # Step 4: Validate hypothesis with metric correlation
        correlation_data = {}
        if root_cause_service:
            correlation_data = await self.correlator.validate_db_connection_hypothesis(
                root_cause_service,
                incident_start
            )

        # Step 5: Check historical patterns
        historical_match = await self.history.find_similar_incident(
            affected_services=affected_services,
            root_cause_alert=root_cause_alert,
        )

        # Step 6: Compute confidence score
        confidence = self._compute_confidence(
            has_explicit_candidate=bool(root_cause_candidates),
            correlation_data=correlation_data,
            has_historical_match=historical_match is not None,
            alert_count=len(firing_alerts),
            affected_service_count=len(affected_services),
        )

        # Step 7: Select runbook
        recommended_runbook = self._select_runbook(
            root_cause_alert=root_cause_alert,
            root_cause_candidates=root_cause_candidates,
            historical_match=historical_match,
        )

        # Step 8: Build human-readable description
        description = self._build_description(
            root_cause_service=root_cause_service,
            root_cause_alert=root_cause_alert,
            affected_services=affected_services,
            firing_alerts=firing_alerts,
            correlation_data=correlation_data,
        )

        result = DiagnosisResult(
            incident_id=incident_id,
            root_cause_alert=root_cause_alert,
            root_cause_service=root_cause_service,
            root_cause_description=description,
            affected_services=affected_services,
            confidence_score=confidence,
            alert_count=len(bundle.alerts),
            deduplicated_alert_count=len(firing_alerts),
            recommended_runbook=recommended_runbook,
            supporting_metrics=correlation_data,
            historical_match=historical_match,
            created_at=datetime.utcnow(),
        )

        await self.history.store_incident(result, bundle)

        log.info(
            "diagnosis_complete",
            incident_id=incident_id,
            confidence=confidence,
            root_cause=root_cause_service,
        )

        return result

    def _compute_confidence(
        self,
        has_explicit_candidate: bool,
        correlation_data: dict,
        has_historical_match: bool,
        alert_count: int,
        affected_service_count: int,
    ) -> float:
        score = 0.0

        if has_explicit_candidate:
            score += 0.40

        if correlation_data.get("supports_hypothesis"):
            score += 0.30
        elif correlation_data.get("correlation_coefficient", 0) > 0.5:
            score += 0.15

        if has_historical_match:
            score += 0.20

        # Log-scaled bonus for alert volume (caps at 0.10)
        score += min(math.log(alert_count + 1) / 20, 0.10)

        return round(min(score, 1.0), 3)

    def _select_runbook(
        self,
        root_cause_alert: Optional[str],
        root_cause_candidates: list,
        historical_match: Optional[str],
    ) -> Optional[str]:
        if root_cause_candidates:
            runbook = root_cause_candidates[0].labels.runbook
            if runbook:
                return runbook

        if historical_match:
            return historical_match

        runbook_map = {
            "DatabaseConnectionPoolExhausted": "RB-DB-001",
            "ServiceErrorRateHigh": "RB-APP-001",
            "KafkaConsumerLagHigh": "RB-KAFKA-001",
            "ServiceLatencyHigh": "RB-APP-002",
        }
        return runbook_map.get(root_cause_alert)

    def _build_description(
        self,
        root_cause_service: Optional[str],
        root_cause_alert: Optional[str],
        affected_services: list[str],
        firing_alerts: list,
        correlation_data: dict,
    ) -> str:
        lines = []

        if root_cause_service and root_cause_alert:
            lines.append(f"Root cause: {root_cause_alert} on {root_cause_service}")

        if affected_services:
            lines.append(f"Cascade affecting: {', '.join(affected_services)}")

        if correlation_data.get("supports_hypothesis"):
            r = correlation_data.get("correlation_coefficient", 0)
            lines.append(
                f"Metric correlation confirms DB connection exhaustion hypothesis (r={r:.2f})"
            )

        critical_count = sum(
            1 for a in firing_alerts
            if a.labels.severity == AlertSeverity.CRITICAL
        )
        if critical_count:
            lines.append(f"{critical_count} critical alerts deduplicated into this incident")

        return ". ".join(lines) + "."
