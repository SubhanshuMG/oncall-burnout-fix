# src/models.py
# https://github.com/SubhanshuMG/oncall-burnout-fix
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
from enum import Enum


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class AlertLabel(BaseModel):
    alertname: str
    severity: AlertSeverity
    layer: str
    impact_type: str
    root_cause_candidate: bool = False
    service: Optional[str] = None
    namespace: str = "default"
    runbook: Optional[str] = None


class Alert(BaseModel):
    status: Literal["firing", "resolved"]
    labels: AlertLabel
    annotations: dict
    starts_at: datetime = Field(alias="startsAt")
    ends_at: Optional[datetime] = Field(None, alias="endsAt")
    generator_url: str = Field("", alias="generatorURL")
    fingerprint: str = ""

    class Config:
        populate_by_name = True


class AlertBundle(BaseModel):
    """What Alertmanager sends us as a group"""
    version: str = "4"
    group_key: str = Field(alias="groupKey")
    status: Literal["firing", "resolved"]
    receiver: str
    group_labels: dict = Field(alias="groupLabels")
    common_labels: dict = Field(alias="commonLabels")
    common_annotations: dict = Field(alias="commonAnnotations")
    alerts: list[Alert]

    class Config:
        populate_by_name = True


class DiagnosisResult(BaseModel):
    incident_id: str
    root_cause_alert: Optional[str]
    root_cause_service: Optional[str]
    root_cause_description: str
    affected_services: list[str]
    confidence_score: float           # 0.0 to 1.0
    alert_count: int
    deduplicated_alert_count: int
    recommended_runbook: Optional[str]
    supporting_metrics: dict
    historical_match: Optional[str]
    created_at: datetime
