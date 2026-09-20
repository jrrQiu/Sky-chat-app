"""Temporal boundary placeholder.

The V1 service uses an in-memory approval store. This module is the integration
point for Temporal Signals, timers, cancellation, and human-in-the-loop resume.
"""

from typing import Any


async def create_approval_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow_id": payload.get("request_id"),
        "status": "pending",
        "provider": "in-memory-v1",
    }


async def signal_approval_decision(
    workflow_id: str,
    decision: str,
) -> dict[str, Any]:
    return {
        "workflow_id": workflow_id,
        "decision": decision,
        "provider": "in-memory-v1",
    }
