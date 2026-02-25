# main.py
# https://github.com/SubhanshuMG/oncall-burnout-fix
from fastapi import FastAPI, HTTPException, Header
from contextlib import asynccontextmanager
import structlog

from src.models import AlertBundle, DiagnosisResult
from src.diagnosis import DiagnosisEngine
from src.aiops import AIOpsTriageEngine
from src.runbook_handler import router as runbook_router

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("diagnosis_engine_starting")
    yield
    log.info("diagnosis_engine_stopping")


app = FastAPI(
    title="On-Call Diagnosis Engine",
    description=(
        "Automated incident root cause analysis. "
        "Source: https://github.com/SubhanshuMG/oncall-burnout-fix"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(runbook_router)

diagnosis_engine = DiagnosisEngine()
aiops_engine = AIOpsTriageEngine()


@app.post("/alerts", response_model=DiagnosisResult)
async def receive_alerts(
    bundle: AlertBundle,
    authorization: str = Header(None),
):
    """
    Primary webhook endpoint. Receives alert bundle from Alertmanager,
    runs automated diagnosis, runs AIOps triage, sends single Slack page.
    """
    log.info(
        "alert_bundle_received",
        group_key=bundle.group_key,
        alert_count=len(bundle.alerts),
        status=bundle.status,
    )

    if bundle.status == "resolved":
        log.info("incident_resolved", group_key=bundle.group_key)
        return {"message": "resolved"}

    try:
        diagnosis = await diagnosis_engine.diagnose(bundle)
        triage = await aiops_engine.triage(diagnosis, bundle)
        await aiops_engine.notify_oncall(triage, diagnosis)
        return diagnosis

    except Exception as e:
        log.error("diagnosis_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}
