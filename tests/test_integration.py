# tests/test_integration.py
# https://github.com/SubhanshuMG/oncall-burnout-fix
#
# End-to-end integration tests. Requires the diagnosis-engine to be running:
#   docker-compose up -d
#   pytest tests/test_integration.py -v --asyncio-mode=auto

import pytest
import httpx

BASE_URL = "http://localhost:8080"

# Exact payload that Alertmanager sends for the Black Friday payment cascade scenario
PAYMENT_CASCADE_BUNDLE = {
    "version": "4",
    "groupKey": '{}/{namespace="production"}:{alertname="~"}',
    "status": "firing",
    "receiver": "diagnosis-engine",
    "groupLabels": {
        "namespace": "production",
        "layer": "infrastructure"
    },
    "commonLabels": {
        "namespace": "production",
        "env": "production"
    },
    "commonAnnotations": {},
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "DatabaseConnectionPoolExhausted",
                "severity": "critical",
                "layer": "infrastructure",
                "impact_type": "resource",
                "root_cause_candidate": "true",
                "service": "order-service",
                "namespace": "production",
                "runbook": "RB-DB-001"
            },
            "annotations": {
                "summary": "DB connection pool at 98% capacity",
                "description": "order-service exhausting Postgres connections"
            },
            "startsAt": "2024-11-29T23:47:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://prometheus/graph",
            "fingerprint": "fp001"
        },
        {
            "status": "firing",
            "labels": {
                "alertname": "ServiceErrorRateHigh",
                "severity": "warning",
                "layer": "application",
                "impact_type": "availability",
                "root_cause_candidate": "false",
                "service": "payment-service",
                "namespace": "production",
                "runbook": "RB-APP-001"
            },
            "annotations": {"summary": "payment-service error rate 12%"},
            "startsAt": "2024-11-29T23:47:30Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://prometheus/graph",
            "fingerprint": "fp002"
        },
        {
            "status": "firing",
            "labels": {
                "alertname": "ServiceLatencyHigh",
                "severity": "warning",
                "layer": "application",
                "impact_type": "performance",
                "root_cause_candidate": "false",
                "service": "checkout-service",
                "namespace": "production",
                "runbook": "RB-APP-002"
            },
            "annotations": {"summary": "checkout-service p99 latency 8.2s"},
            "startsAt": "2024-11-29T23:47:45Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://prometheus/graph",
            "fingerprint": "fp003"
        }
    ]
}


@pytest.mark.asyncio
async def test_full_pipeline_payment_cascade():
    """
    Simulate the Black Friday payment cascade.
    Engine should correctly identify order-service DB exhaustion as root cause.
    31 raw alerts in, 1 incident out.
    """
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        response = await client.post("/alerts", json=PAYMENT_CASCADE_BUNDLE)

    assert response.status_code == 200
    result = response.json()

    assert result["root_cause_service"] == "order-service"
    assert result["root_cause_alert"] == "DatabaseConnectionPoolExhausted"
    assert "payment-service" in result["affected_services"]
    assert "checkout-service" in result["affected_services"]
    assert result["confidence_score"] >= 0.7
    assert result["recommended_runbook"] == "RB-DB-001"
    assert result["alert_count"] == 3


@pytest.mark.asyncio
async def test_health_endpoint():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=5.0) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_resolved_bundle_handled_gracefully():
    resolved_bundle = {**PAYMENT_CASCADE_BUNDLE, "status": "resolved"}
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        response = await client.post("/alerts", json=resolved_bundle)
    assert response.status_code == 200
