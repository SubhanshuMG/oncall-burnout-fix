# src/correlator.py
# https://github.com/SubhanshuMG/oncall-burnout-fix
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

log = structlog.get_logger()

PROMETHEUS_URL = "http://prometheus-operated.monitoring.svc.cluster.local:9090"


class MetricCorrelator:
    """
    Fetches time series data from Prometheus and computes
    correlation between metrics to validate root cause hypotheses.
    """

    def __init__(self, prometheus_url: str = PROMETHEUS_URL):
        self.prometheus_url = prometheus_url
        self.client = httpx.AsyncClient(timeout=10.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
    async def fetch_metric(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "30s"
    ) -> list[float]:
        """Fetch metric time series from Prometheus range query API"""
        params = {
            "query": query,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step,
        }
        try:
            response = await self.client.get(
                f"{self.prometheus_url}/api/v1/query_range",
                params=params
            )
            response.raise_for_status()
            data = response.json()

            if data["status"] != "success":
                log.warning("prometheus_query_failed", query=query, status=data["status"])
                return []

            results = data.get("data", {}).get("result", [])
            if not results:
                return []

            return [float(v[1]) for v in results[0]["values"]]

        except Exception as e:
            log.error("prometheus_fetch_error", query=query, error=str(e))
            return []

    async def compute_correlation(
        self,
        metric_a: list[float],
        metric_b: list[float]
    ) -> float:
        """Pearson correlation coefficient between two metric series"""
        if len(metric_a) < 3 or len(metric_b) < 3:
            return 0.0

        min_len = min(len(metric_a), len(metric_b))
        a = np.array(metric_a[:min_len])
        b = np.array(metric_b[:min_len])

        if np.std(a) == 0 or np.std(b) == 0:
            return 0.0

        correlation = np.corrcoef(a, b)[0, 1]
        return float(correlation) if not np.isnan(correlation) else 0.0

    async def compute_anomaly_score(self, values: list[float]) -> float:
        """
        Z-score based anomaly detection.
        Returns how many standard deviations the latest value is from the
        historical mean, normalized to a 0–1 range. Higher = more anomalous.
        """
        if len(values) < 5:
            return 0.0

        historical = np.array(values[:-1])
        latest = values[-1]
        mean = np.mean(historical)
        std = np.std(historical)

        if std == 0:
            return 0.0

        z_score = abs((latest - mean) / std)
        return float(min(z_score / 5.0, 1.0))

    async def validate_db_connection_hypothesis(
        self,
        service: str,
        incident_start: datetime
    ) -> dict:
        """
        Check if DB connection exhaustion is correlated with service errors.
        Returns a dict with correlation data to support or refute the hypothesis.
        """
        end = incident_start + timedelta(minutes=10)
        start = incident_start - timedelta(minutes=5)

        db_query = f'pg_stat_activity_count{{service="{service}",state="active"}}'
        error_query = f'rate(http_requests_total{{service="{service}",status=~"5.."}}[2m])'

        db_values = await self.fetch_metric(db_query, start, end)
        error_values = await self.fetch_metric(error_query, start, end)

        correlation = await self.compute_correlation(db_values, error_values)
        db_anomaly = await self.compute_anomaly_score(db_values)

        return {
            "hypothesis": "db_connection_exhaustion",
            "correlation_coefficient": correlation,
            "db_anomaly_score": db_anomaly,
            "supports_hypothesis": correlation > 0.7 and db_anomaly > 0.6,
            "db_connection_values": db_values[-3:] if db_values else [],
            "error_rate_values": error_values[-3:] if error_values else [],
        }

    async def close(self):
        await self.client.aclose()
