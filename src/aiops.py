# src/aiops.py
# https://github.com/SubhanshuMG/oncall-burnout-fix
import os
import httpx
import json
import structlog
from datetime import datetime
from typing import Optional

from .models import DiagnosisResult, AlertBundle

log = structlog.get_logger()

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


class AIOpsTriageEngine:
    """
    Uses an LLM to:
      1. Generate a natural-language incident summary
      2. Suggest specific remediation steps from the runbook
      3. Format a Slack message with actionable approve/escalate buttons

    Falls back gracefully to structured data if LLM is unavailable.
    """

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def triage(
        self,
        diagnosis: DiagnosisResult,
        bundle: AlertBundle,
    ) -> dict:
        """Generate LLM-powered triage package"""
        prompt = self._build_triage_prompt(diagnosis, bundle)

        try:
            response = await self.client.post(
                OPENAI_API_URL,
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4-turbo-preview",
                    "temperature": 0.1,  # Low temp: deterministic, factual output
                    "max_tokens": 800,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are an expert SRE assistant. "
                                "Given an incident diagnosis, produce a clear, concise "
                                "JSON triage package. Be specific, not generic. "
                                "Use technical language appropriate for a senior engineer "
                                "reading this at 3AM. No fluff, no padding."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)

        except Exception as e:
            log.error("aiops_triage_failed", error=str(e))
            return self._fallback_triage(diagnosis)

    def _build_triage_prompt(
        self,
        diagnosis: DiagnosisResult,
        bundle: AlertBundle,
    ) -> str:
        alert_names = [a.labels.alertname for a in bundle.alerts if a.status == "firing"]
        return f"""
Incident diagnosis data:

Root cause alert: {diagnosis.root_cause_alert}
Root cause service: {diagnosis.root_cause_service}
Root cause description: {diagnosis.root_cause_description}
Affected services: {', '.join(diagnosis.affected_services)}
Alert count (before dedup): {diagnosis.alert_count}
Alert count (after dedup): {diagnosis.deduplicated_alert_count}
Confidence score: {diagnosis.confidence_score:.0%}
Recommended runbook: {diagnosis.recommended_runbook}
Historical match: {diagnosis.historical_match or 'None'}
Individual alerts: {', '.join(alert_names[:10])}

Produce a JSON object with these exact keys:
{{
  "one_line_summary": "Single sentence. What is broken and why.",
  "what_is_happening": "2-3 sentences. Explain the cascade simply.",
  "immediate_action": "The single most important thing to do RIGHT NOW.",
  "steps": ["step 1", "step 2", "step 3"],
  "do_not_do": "One common mistake to avoid in this scenario.",
  "estimated_resolution_time": "Your best estimate in minutes",
  "escalate_if": "Condition that means this needs a second person"
}}
"""

    def _fallback_triage(self, diagnosis: DiagnosisResult) -> dict:
        """Graceful degradation when LLM is unavailable"""
        return {
            "one_line_summary": diagnosis.root_cause_description,
            "what_is_happening": (
                f"Cascade from {diagnosis.root_cause_service} "
                f"affecting {len(diagnosis.affected_services)} services."
            ),
            "immediate_action": f"Check runbook {diagnosis.recommended_runbook}",
            "steps": [
                "Check root cause service logs",
                "Verify metric stabilization",
                "Review runbook",
            ],
            "do_not_do": "Do not restart all services simultaneously",
            "estimated_resolution_time": "15-30",
            "escalate_if": "Issue persists after 30 minutes",
        }

    async def notify_oncall(self, triage: dict, diagnosis: DiagnosisResult):
        """
        Send a single, rich Slack message to the on-call engineer.
        Replaces the storm of individual PagerDuty pages.
        """
        confidence_emoji = (
            "🟢" if diagnosis.confidence_score > 0.8
            else "🟡" if diagnosis.confidence_score > 0.6
            else "🔴"
        )

        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"INCIDENT {diagnosis.incident_id.upper()} — {triage['one_line_summary']}",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Root Cause:*\n`{diagnosis.root_cause_alert}` on `{diagnosis.root_cause_service}`",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Confidence:*\n{confidence_emoji} {diagnosis.confidence_score:.0%}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Affected Services:*\n{', '.join(f'`{s}`' for s in diagnosis.affected_services)}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Alerts Deduplicated:*\n{diagnosis.deduplicated_alert_count} → 1 page",
                        },
                    ],
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*What is happening:*\n{triage['what_is_happening']}",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*Immediate action:*\n{triage['immediate_action']}\n\n"
                            f"*Steps:*\n"
                            + "\n".join(f"{i+1}. {s}" for i, s in enumerate(triage["steps"]))
                            + f"\n\n*Do NOT:* {triage['do_not_do']}"
                        ),
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Runbook:* `{diagnosis.recommended_runbook}`",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Est. Resolution:* {triage['estimated_resolution_time']} min",
                        },
                    ],
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Approve Auto-Fix"},
                            "style": "primary",
                            "value": json.dumps({
                                "action": "auto_fix",
                                "incident_id": diagnosis.incident_id,
                                "runbook": diagnosis.recommended_runbook,
                            }),
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Run Manually"},
                            "value": json.dumps({
                                "action": "manual",
                                "incident_id": diagnosis.incident_id,
                                "runbook": diagnosis.recommended_runbook,
                            }),
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Escalate"},
                            "style": "danger",
                            "value": json.dumps({
                                "action": "escalate",
                                "incident_id": diagnosis.incident_id,
                                "escalate_if": triage["escalate_if"],
                            }),
                        },
                    ],
                },
            ]
        }

        try:
            response = await self.client.post(SLACK_WEBHOOK_URL, json=payload)
            response.raise_for_status()
            log.info("slack_notification_sent", incident_id=diagnosis.incident_id)
        except Exception as e:
            log.error("slack_notification_failed", error=str(e))

    async def close(self):
        await self.client.aclose()
