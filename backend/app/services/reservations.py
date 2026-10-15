from typing import Dict, Any

from fastapi import HTTPException
from sqlalchemy import text

from app.core.database_pool import db_pool


async def get_properties(tenant_id: str):
    async with db_pool.get_session() as session:
        result = await session.execute(text("""
            SELECT id, name, timezone FROM properties
            WHERE tenant_id = :tenant_id ORDER BY id
        """), {"tenant_id": tenant_id})
        return [dict(row) for row in result.mappings()]


async def get_owned_property(property_id: str, tenant_id: str):
    async with db_pool.get_session() as session:
        result = await session.execute(text("""
            SELECT id, name, timezone FROM properties
            WHERE id = :property_id AND tenant_id = :tenant_id
        """), {"property_id": property_id, "tenant_id": tenant_id})
        property_data = result.mappings().first()
        if property_data is None:
            raise HTTPException(status_code=404, detail="Property not found")
        return dict(property_data)


async def calculate_total_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """Aggregate actual reservations; database failures must never become revenue."""
    async with db_pool.get_session() as session:
        result = await session.execute(text("""
            SELECT COALESCE(SUM(total_amount), 0) AS total_revenue,
                   COUNT(*) AS reservation_count
            FROM reservations
            WHERE property_id = :property_id AND tenant_id = :tenant_id
        """), {"property_id": property_id, "tenant_id": tenant_id})
        row = result.one()
        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "total": str(row.total_revenue),
            "currency": "USD",
            "count": row.reservation_count,
        }
