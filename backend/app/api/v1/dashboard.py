from fastapi import APIRouter, Depends, HTTPException
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
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    
    tenant_id = require_tenant(current_user)
    
    try:
        revenue_data = await get_revenue_summary(property_id, tenant_id)
    except (SQLAlchemyError, OSError, RuntimeError):
        logger.exception("Unable to load dashboard revenue")
        raise HTTPException(status_code=503, detail="Revenue data temporarily unavailable")
    
    total_revenue_float = float(revenue_data['total'])
    
    return {
        "property_id": revenue_data['property_id'],
        "total_revenue": total_revenue_float,
        "currency": revenue_data['currency'],
        "reservations_count": revenue_data['count']
    }
