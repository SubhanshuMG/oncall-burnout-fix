# src/runbook_handler.py
# https://github.com/SubhanshuMG/oncall-burnout-fix
from fastapi import APIRouter, Request
import json
import asyncio
import structlog

log = structlog.get_logger()
router = APIRouter()

RUNBOOK_PLAYBOOKS = {
    "RB-DB-001": "playbooks/db-connection-pool-fix.yml",
    "RB-APP-001": "playbooks/app-restart-graceful.yml",
    "RB-KAFKA-001": "playbooks/kafka-consumer-reset.yml",
    "RB-APP-002": "playbooks/app-scale-horizontal.yml",
}


@router.post("/slack/actions")
async def handle_slack_action(request: Request):
    """
    Handles button clicks from the Slack incident message.
    Slack sends a URL-encoded payload with the action value JSON.
    """
    form_data = await request.form()
    payload = json.loads(form_data["payload"])
    action = payload["actions"][0]
    action_data = json.loads(action["value"])

    action_type = action_data["action"]
    incident_id = action_data["incident_id"]
    runbook = action_data.get("runbook")

    log.info(
        "slack_action_received",
        action=action_type,
        incident_id=incident_id,
        runbook=runbook,
    )

    if action_type == "auto_fix" and runbook in RUNBOOK_PLAYBOOKS:
        # Fire-and-forget: respond to Slack immediately, run async
        asyncio.create_task(
            execute_runbook(
                runbook_id=runbook,
                incident_id=incident_id,
                triggered_by=payload["user"]["name"],
            )
        )
        return {
            "text": f"Auto-fix triggered. Runbook `{runbook}` executing for incident `{incident_id}`."
        }

    elif action_type == "escalate":
        return {"text": f"Escalating incident `{incident_id}`. Waking secondary on-call."}

    elif action_type == "manual":
        playbook = RUNBOOK_PLAYBOOKS.get(runbook, "unknown")
        return {
            "text": (
                f"Manual mode. Run this locally:\n"
                f"```ansible-playbook {playbook} -e incident_id={incident_id}```"
            )
        }

    return {"text": "Action acknowledged."}


async def execute_runbook(runbook_id: str, incident_id: str, triggered_by: str):
    """Execute an Ansible playbook and stream output to structured logs"""
    playbook = RUNBOOK_PLAYBOOKS.get(runbook_id)
    if not playbook:
        log.error("unknown_runbook", runbook_id=runbook_id)
        return

    cmd = [
        "ansible-playbook",
        playbook,
        "-e", f"incident_id={incident_id}",
        "-e", f"triggered_by={triggered_by}",
        "-e", f"runbook_id={runbook_id}",
        "--diff",  # Show exactly what changed (essential for postmortems)
    ]

    log.info("runbook_execution_started", cmd=" ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode()

        if proc.returncode == 0:
            log.info(
                "runbook_execution_succeeded",
                incident_id=incident_id,
                output=output[-500:],
            )
        else:
            log.error(
                "runbook_execution_failed",
                incident_id=incident_id,
                output=output[-500:],
            )

    except Exception as e:
        log.error("runbook_execution_error", error=str(e))
