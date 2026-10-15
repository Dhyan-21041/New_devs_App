from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Any
from app.services.cache import get_revenue_summary
from app.core.auth import authenticate_request as get_current_user
from app.services.reservations import get_properties
from sqlalchemy.exc import SQLAlchemyError
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


def require_tenant(current_user):
    tenant_id = getattr(current_user, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=403, detail="Tenant context required")
    return tenant_id


@router.get("/dashboard/properties")
async def get_dashboard_properties(current_user=Depends(get_current_user)):
    tenant_id = require_tenant(current_user)
    try:
        return await get_properties(tenant_id)
    except (SQLAlchemyError, OSError, RuntimeError):
        logger.exception("Unable to load dashboard properties")
        raise HTTPException(status_code=503, detail="Property data temporarily unavailable")

@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    month: int | None = Query(default=None, ge=1, le=12),
    year: int | None = Query(default=None, ge=2, le=9998),
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    
    tenant_id = require_tenant(current_user)
    if (month is None) != (year is None):
        raise HTTPException(status_code=422, detail="Provide both month and year")
    
    try:
        revenue_data = await get_revenue_summary(property_id, tenant_id, month, year)
    except (SQLAlchemyError, OSError, RuntimeError):
        logger.exception("Unable to load dashboard revenue")
        raise HTTPException(status_code=503, detail="Revenue data temporarily unavailable")
    
    return {
        "property_id": revenue_data['property_id'],
        "total_revenue": revenue_data['total'],
        "currency": revenue_data['currency'],
        "reservations_count": revenue_data['count'],
        "month": revenue_data['month'],
        "year": revenue_data['year'],
        "timezone": revenue_data['timezone'],
    }
