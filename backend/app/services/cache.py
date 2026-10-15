import json
import redis.asyncio as redis
from typing import Dict, Any
import logging

from app.config import settings
from app.services.reservations import get_owned_property, calculate_total_revenue, calculate_monthly_revenue

logger = logging.getLogger(__name__)

# Initialize Redis client (typically configured centrally).
redis_client = redis.Redis.from_url(settings.redis_url)

async def get_revenue_summary(property_id: str, tenant_id: str, month=None, year=None) -> Dict[str, Any]:
    """
    Fetches revenue summary, utilizing caching to improve performance.
    """
    # Authorize before cache access, even when a summary is already cached.
    property_data = await get_owned_property(property_id, tenant_id)
    period = f"{year:04d}-{month:02d}" if month is not None else "all-time"
    cache_key = f"revenue:v3:{tenant_id}:{property_id}:{property_data['timezone']}:{period}"
    
    # Try to get from cache
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            if (isinstance(data, dict) and data.get("tenant_id") == tenant_id
                    and data.get("property_id") == property_id
                    and data.get("month") == month and data.get("year") == year
                    and data.get("timezone") == property_data["timezone"]):
                return data
    except (redis.RedisError, ValueError):
        logger.warning("Revenue cache unavailable or invalid; querying database")
    
    # Calculate revenue
    if month is not None:
        result = await calculate_monthly_revenue(property_id, tenant_id, month, year, property_data["timezone"])
    else:
        result = await calculate_total_revenue(property_id, tenant_id)
    result.update(month=month, year=year, timezone=property_data["timezone"])
    
    # Cache the result for 5 minutes
    try:
        await redis_client.setex(cache_key, 300, json.dumps(result))
    except redis.RedisError:
        logger.warning("Revenue cache write unavailable")
    
    return result
