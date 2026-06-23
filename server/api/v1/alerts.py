"""
NexLink Server -- Alert API Endpoints
======================================
GET  /api/v1/alerts                    -- list alerts (filterable, paginated)
POST /api/v1/alerts/{alert_id}/resolve -- resolve an alert

Alerts are actionable notifications raised when a device exceeds health
thresholds, loses connectivity, or encounters an agent issue.
They differ from events: alerts require attention and can be resolved.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.deps import get_current_serial, get_db
from server.db.models.alert import Alert
from server.schemas.alert import AlertListResponse, AlertSchema, ResolveAlertRequest
from server.schemas.common import MessageResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get(
    "",
    response_model=AlertListResponse,
    summary="List alerts",
    description="""
Return paginated alerts with optional filters.

Filters:
- serial: filter by device serial_number
- severity: warning, error, or critical
- is_resolved: true/false
    """,
)
async def list_alerts(
    serial: str | None = Query(None, description="Filter by device serial_number"),
    severity: str | None = Query(None, description="Filter by severity: warning, error, critical"),
    is_resolved: bool | None = Query(None, description="Filter by resolution status"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(get_current_serial),
) -> AlertListResponse:
    """Return paginated alerts with optional filters."""
    query = select(Alert)
    count_query = select(func.count()).select_from(Alert)

    if serial is not None:
        query = query.where(Alert.serial_number == serial)
        count_query = count_query.where(Alert.serial_number == serial)

    if severity is not None:
        query = query.where(Alert.severity == severity)
        count_query = count_query.where(Alert.severity == severity)

    if is_resolved is not None:
        query = query.where(Alert.is_resolved == is_resolved)
        count_query = count_query.where(Alert.is_resolved == is_resolved)

    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Alert.created_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await db.execute(query)
    rows = result.scalars().all()

    return AlertListResponse(
        alerts=[AlertSchema.model_validate(a) for a in rows],
        total=total,
    )


@router.post(
    "/{alert_id}/resolve",
    response_model=MessageResponse,
    summary="Resolve an alert",
    description="Mark an alert as resolved. Sets is_resolved=True and records who resolved it.",
)
async def resolve_alert(
    alert_id: int,
    req: ResolveAlertRequest | None = None,
    db: AsyncSession = Depends(get_db),
    auth_serial: str = Depends(get_current_serial),
) -> MessageResponse:
    """Mark an alert as resolved."""
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id)
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if alert.is_resolved:
        raise HTTPException(status_code=409, detail="Alert already resolved")

    resolved_by = (req.resolved_by if req and req.resolved_by else auth_serial)

    alert.is_resolved = True
    alert.resolved_at = datetime.now(timezone.utc)
    alert.resolved_by = resolved_by
    await db.flush()

    logger.info("Alert resolved: id=%d by=%s", alert_id, resolved_by)

    return MessageResponse(message=f"Alert {alert_id} resolved")
