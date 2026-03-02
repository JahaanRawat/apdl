"""Approval endpoint — approve or reject a pending agent action."""

from __future__ import annotations

import logging
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/agents", tags=["agents"])


class ApprovalRequest(BaseModel):
    approved: bool
    comment: str | None = None


class ApprovalResponse(BaseModel):
    run_id: str
    status: str
    message: str


@router.post("/{run_id}/approve", response_model=ApprovalResponse)
async def approve_action(
    run_id: str,
    body: ApprovalRequest,
    request: Request,
) -> ApprovalResponse:
    """Approve or reject a pending agent action.

    When an agent run reaches an approval gate (human-in-the-loop interrupt),
    this endpoint records the decision and updates the run status so the
    supervisor can resume.
    """
    pool: asyncpg.Pool = request.app.state.pg_pool

    async with pool.acquire() as conn:
        row: Any = await conn.fetchrow(
            "SELECT run_id, status, phase FROM agent_runs WHERE run_id = $1",
            run_id,
        )

        if row is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        if row["status"] != "waiting_approval":
            raise HTTPException(
                status_code=400,
                detail=f"Run {run_id} is not waiting for approval (current status: {row['status']})",
            )

        new_status = "approved" if body.approved else "rejected"
        new_phase = "resuming" if body.approved else "completed"

        await conn.execute(
            """
            UPDATE agent_runs
            SET status = $2, phase = $3, updated_at = now()
            WHERE run_id = $1
            """,
            run_id,
            new_status,
            new_phase,
        )

        # Record in audit log
        await conn.execute(
            """
            INSERT INTO agent_audit_log (run_id, action_type, config, approval_status)
            VALUES ($1, 'human_approval', $2, $3)
            """,
            run_id,
            {"comment": body.comment},
            new_status,
        )

    action_word = "approved" if body.approved else "rejected"
    logger.info("Run %s %s by human reviewer", run_id, action_word)

    return ApprovalResponse(
        run_id=run_id,
        status=new_status,
        message=f"Action {action_word} successfully.",
    )
