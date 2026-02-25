# src/history.py
# https://github.com/SubhanshuMG/oncall-burnout-fix
import json
import redis.asyncio as redis
import structlog
from typing import Optional
from datetime import datetime

from .models import DiagnosisResult, AlertBundle

log = structlog.get_logger()
REDIS_URL = "redis://redis.monitoring.svc.cluster.local:6379"


class IncidentHistory:
    """
    Stores incident patterns in Redis for future matching.

    Uses a fingerprint based on (sorted affected services + root cause alert)
    to identify similar past incidents and their successful remediations.
    Patterns expire after 90 days.
    """

    def __init__(self):
        self._client: Optional[redis.Redis] = None

    async def _get_client(self) -> redis.Redis:
        if not self._client:
            self._client = await redis.from_url(REDIS_URL, decode_responses=True)
        return self._client

    def _compute_fingerprint(
        self,
        affected_services: list[str],
        root_cause_alert: Optional[str]
    ) -> str:
        services_key = ",".join(sorted(affected_services))
        return f"{root_cause_alert or 'unknown'}::{services_key}"

    async def find_similar_incident(
        self,
        affected_services: list[str],
        root_cause_alert: Optional[str],
    ) -> Optional[str]:
        """Returns the recommended runbook from the most recent similar incident"""
        try:
            client = await self._get_client()
            fingerprint = self._compute_fingerprint(affected_services, root_cause_alert)
            result = await client.hget(f"incident:pattern:{fingerprint}", "recommended_runbook")
            if result:
                log.info("historical_pattern_matched", fingerprint=fingerprint)
            return result
        except Exception as e:
            log.warning("history_lookup_failed", error=str(e))
            return None

    async def store_incident(self, result: DiagnosisResult, bundle: AlertBundle):
        """Store incident fingerprint for future pattern matching"""
        try:
            client = await self._get_client()
            fingerprint = self._compute_fingerprint(
                result.affected_services,
                result.root_cause_alert,
            )
            key = f"incident:pattern:{fingerprint}"
            current_count = await client.hget(key, "occurrence_count")
            await client.hset(key, mapping={
                "root_cause_alert": result.root_cause_alert or "",
                "root_cause_service": result.root_cause_service or "",
                "recommended_runbook": result.recommended_runbook or "",
                "confidence": str(result.confidence_score),
                "last_seen": datetime.utcnow().isoformat(),
                "occurrence_count": str(int(current_count or "0") + 1),
            })
            await client.expire(key, 60 * 60 * 24 * 90)  # 90-day TTL
        except Exception as e:
            log.warning("history_store_failed", error=str(e))
