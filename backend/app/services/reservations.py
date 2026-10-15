from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

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


async def calculate_monthly_revenue(
    property_id: str, tenant_id: str, month: int, year: int, property_timezone: str
) -> dict[str, Any]:
    """Use a half-open property-local month, with each boundary converted to UTC."""
    zone = ZoneInfo(property_timezone)
    start = datetime(year, month, 1, tzinfo=zone)
    end = datetime(year + 1, 1, 1, tzinfo=zone) if month == 12 else datetime(year, month + 1, 1, tzinfo=zone)
    return await calculate_total_revenue(
        property_id, tenant_id, start.astimezone(UTC), end.astimezone(UTC)
    )


async def calculate_total_revenue(
    property_id: str, tenant_id: str, start_date=None, end_date=None
) -> dict[str, Any]:
    """Aggregate actual reservations; database failures must never become revenue."""
    async with db_pool.get_session() as session:
        period_filter = "AND check_in_date >= :start_date AND check_in_date < :end_date" if start_date else ""
        params: dict[str, Any] = {"property_id": property_id, "tenant_id": tenant_id}
        if start_date:
            params.update(start_date=start_date, end_date=end_date)
        result = await session.execute(text(f"""
            SELECT currency, SUM(total_amount) AS total_revenue,
                   COUNT(*) AS reservation_count
            FROM reservations
            WHERE property_id = :property_id AND tenant_id = :tenant_id
            {period_filter}
            GROUP BY currency
        """), params)
        rows = result.all()
        if len(rows) > 1:
            raise HTTPException(status_code=422, detail="Revenue contains multiple currencies; a single total is unavailable")
        row = rows[0] if rows else None
        total = row.total_revenue if row else Decimal(0)
        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "total": format(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f"),
            "currency": row.currency if row else "USD",
            "count": row.reservation_count if row else 0,
        }
