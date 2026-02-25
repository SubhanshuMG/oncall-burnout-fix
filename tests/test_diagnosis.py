# tests/test_diagnosis.py
# https://github.com/SubhanshuMG/oncall-burnout-fix
import pytest
import pytest_asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

from src.models import AlertBundle, AlertSeverity
from src.diagnosis import DiagnosisEngine
from src.graph import ServiceDependencyGraph


def make_alert(
    alertname: str,
    service: str,
    severity: str = "warning",
    root_cause_candidate: bool = False,
    layer: str = "application",
) -> dict:
    """Helper: build a raw alert dict matching Alertmanager webhook format"""
    return {
        "status": "firing",
        "labels": {
            "alertname": alertname,
            "severity": severity,
            "layer": layer,
            "impact_type": "availability",
            "root_cause_candidate": str(root_cause_candidate).lower(),
            "service": service,
            "namespace": "production",
            "runbook": f"RB-{alertname[:3].upper()}-001",
        },
        "annotations": {"summary": f"Test alert for {service}"},
        "startsAt": datetime.utcnow().isoformat() + "Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "http://prometheus/",
        "fingerprint": f"fp-{alertname}-{service}",
    }


def make_bundle(alerts: list[dict]) -> dict:
    return {
        "version": "4",
        "groupKey": "{}:{alertname='test'}",
        "status": "firing",
        "receiver": "diagnosis-engine",
        "groupLabels": {"namespace": "production"},
        "commonLabels": {"namespace": "production"},
        "commonAnnotations": {},
        "alerts": alerts,
    }


class TestServiceDependencyGraph:
    def setup_method(self):
        self.graph = ServiceDependencyGraph()

    def test_get_upstream_services(self):
        upstream = self.graph.get_upstream_services("order-service")
        assert "payment-service" in upstream
        assert "checkout-service" in upstream

    def test_get_downstream_services(self):
        downstream = self.graph.get_downstream_services("order-service")
        assert "postgres-rds" in downstream
        assert "kafka" in downstream

    def test_find_root_cause_single_service(self):
        affected = ["payment-service", "checkout-service", "order-service"]
        root = self.graph.find_likely_root_cause(affected)
        assert root == "order-service"

    def test_find_root_cause_unknown_service(self):
        affected = ["nonexistent-service"]
        root = self.graph.find_likely_root_cause(affected)
        assert root is not None

    def test_empty_affected_services(self):
        root = self.graph.find_likely_root_cause([])
        assert root is None

    def test_impact_radius(self):
        impacted = self.graph.get_impact_radius("postgres-rds")
        assert "order-service" in impacted
        assert "inventory-service" in impacted


@pytest.mark.asyncio
class TestDiagnosisEngine:
    async def setup_method(self, method):
        with patch("src.history.redis"):
            self.engine = DiagnosisEngine()
            self.engine.correlator.validate_db_connection_hypothesis = AsyncMock(
                return_value={
                    "hypothesis": "db_connection_exhaustion",
                    "correlation_coefficient": 0.87,
                    "db_anomaly_score": 0.82,
                    "supports_hypothesis": True,
                    "db_connection_values": [85, 92, 98],
                    "error_rate_values": [0.01, 0.08, 0.15],
                }
            )
            self.engine.history.find_similar_incident = AsyncMock(return_value="RB-DB-001")
            self.engine.history.store_incident = AsyncMock()

    async def test_diagnosis_with_explicit_root_cause(self):
        """Alert with root_cause_candidate=true should dominate the diagnosis"""
        raw_bundle = make_bundle([
            make_alert(
                "DatabaseConnectionPoolExhausted",
                "order-service",
                severity="critical",
                root_cause_candidate=True,
                layer="infrastructure",
            ),
            make_alert("ServiceErrorRateHigh", "payment-service"),
            make_alert("ServiceErrorRateHigh", "checkout-service"),
            make_alert("ServiceLatencyHigh", "checkout-service"),
        ])
        bundle = AlertBundle(**raw_bundle)
        result = await self.engine.diagnose(bundle)

        assert result.root_cause_service == "order-service"
        assert result.root_cause_alert == "DatabaseConnectionPoolExhausted"
        assert "payment-service" in result.affected_services
        assert result.confidence_score > 0.7
        assert result.recommended_runbook == "RB-DB-001"

    async def test_diagnosis_without_explicit_root_cause(self):
        """Graph traversal should identify order-service as most likely root"""
        raw_bundle = make_bundle([
            make_alert("ServiceErrorRateHigh", "payment-service"),
            make_alert("ServiceErrorRateHigh", "checkout-service"),
            make_alert("ServiceErrorRateHigh", "order-service"),
            make_alert("ServiceLatencyHigh", "payment-service"),
        ])
        bundle = AlertBundle(**raw_bundle)
        result = await self.engine.diagnose(bundle)

        assert result.root_cause_service == "order-service"
        assert result.alert_count == 4

    async def test_confidence_drops_without_correlation(self):
        """Low metric correlation should reduce confidence score"""
        self.engine.correlator.validate_db_connection_hypothesis = AsyncMock(
            return_value={
                "hypothesis": "db_connection_exhaustion",
                "correlation_coefficient": 0.2,
                "db_anomaly_score": 0.1,
                "supports_hypothesis": False,
            }
        )
        self.engine.history.find_similar_incident = AsyncMock(return_value=None)

        raw_bundle = make_bundle([
            make_alert("ServiceErrorRateHigh", "payment-service"),
        ])
        bundle = AlertBundle(**raw_bundle)
        result = await self.engine.diagnose(bundle)
        assert result.confidence_score < 0.6

    async def test_single_alert_bundle(self):
        """Edge case: single alert should still produce a valid diagnosis"""
        raw_bundle = make_bundle([
            make_alert(
                "DatabaseConnectionPoolExhausted",
                "postgres-rds",
                severity="critical",
                root_cause_candidate=True,
            )
        ])
        bundle = AlertBundle(**raw_bundle)
        result = await self.engine.diagnose(bundle)

        assert result.alert_count == 1
        assert result.incident_id is not None
        assert result.created_at is not None
