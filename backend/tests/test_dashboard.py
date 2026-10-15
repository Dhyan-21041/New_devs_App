"""Dashboard regressions against the supplied PostgreSQL schema and seed data.

Run from backend/: python -m unittest discover -s tests -v
Set DATABASE_URL to a disposable database loaded with database/{schema,seed}.sql.
"""

import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from redis.exceptions import ConnectionError as RedisConnectionError
from fastapi import FastAPI

from app.api.v1 import dashboard, login
from app.core.database_pool import db_pool


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.values[key] = value


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db_pool.initialize()
        self.cache = MemoryRedis()
        self.cache_patch = patch("app.services.cache.redis_client", self.cache)
        self.cache_patch.start()
        self.app = FastAPI()
        self.app.include_router(login.router, prefix="/api/v1")
        self.app.include_router(dashboard.router, prefix="/api/v1")
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        self.headers = {}
        for name, password in (("sunset", "client_a_2024"), ("ocean", "client_b_2024")):
            response = await self.client.post(
                "/api/v1/auth/login",
                json={"email": f"{name}@propertyflow.com", "password": password},
            )
            self.assertEqual(response.status_code, 200)
            self.headers[name] = {"Authorization": f"Bearer {response.json()['access_token']}"}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.cache_patch.stop()
        await db_pool.close()

    async def summary(self, account, property_id="prop-001"):
        return await self.client.get(
            "/api/v1/dashboard/summary",
            params={"property_id": property_id}, headers=self.headers[account],
        )

    async def test_seeded_totals_and_cache_isolation_in_both_orders(self):
        for order in (("sunset", "ocean"), ("ocean", "sunset")):
            self.cache.values.clear()
            for account in order + order:
                response = await self.summary(account)
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                expected = "2250.00" if account == "sunset" else "0.00"
                self.assertEqual(Decimal(str(data["total_revenue"])), Decimal(expected))
                self.assertEqual(data["reservations_count"], 4 if account == "sunset" else 0)

    async def test_property_list_is_tenant_scoped(self):
        for account, expected in (
            ("sunset", {"Beach House Alpha", "City Apartment Downtown", "Country Villa Estate"}),
            ("ocean", {"Mountain Lodge Beta", "Lakeside Cottage", "Urban Loft Modern"}),
        ):
            response = await self.client.get(
                "/api/v1/dashboard/properties", headers=self.headers[account]
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual({item["name"] for item in response.json()}, expected)

    async def test_foreign_property_rejected_on_cold_and_warm_cache(self):
        for owner, stranger, property_id in (
            ("ocean", "sunset", "prop-004"), ("sunset", "ocean", "prop-002")
        ):
            self.cache.values.clear()
            self.assertEqual((await self.summary(stranger, property_id)).status_code, 404)
            self.assertEqual((await self.summary(owner, property_id)).status_code, 200)
            self.assertEqual((await self.summary(stranger, property_id)).status_code, 404)

    async def test_no_authentication_or_tenant_is_rejected(self):
        response = await self.client.get("/api/v1/dashboard/summary?property_id=prop-001")
        self.assertEqual(response.status_code, 401)
        self.app.dependency_overrides[dashboard.get_current_user] = lambda: SimpleNamespace(tenant_id=None)
        self.assertEqual((await self.summary("sunset")).status_code, 403)

    async def test_database_failure_is_not_fabricated_success(self):
        with patch.object(db_pool, "get_session", side_effect=RuntimeError("Database unavailable")), \
                self.assertLogs("app.api.v1.dashboard", level="ERROR"):
            response = await self.summary("sunset")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("1000", response.text)
            self.assertFalse(self.cache.values)

    async def test_cache_payload_identity_is_checked(self):
        await self.summary("sunset")
        for key in self.cache.values:
            self.cache.values[key] = json.dumps({
                "property_id": "prop-001", "tenant_id": "tenant-b",
                "total": "9999.00", "count": 99, "currency": "USD",
            })
        response = await self.summary("sunset")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.json()["total_revenue"])), Decimal("2250.00"))

    async def test_redis_failure_uses_real_database(self):
        with patch.object(self.cache, "get", AsyncMock(side_effect=RedisConnectionError)), \
                patch.object(self.cache, "setex", AsyncMock(side_effect=RedisConnectionError)):
            response = await self.summary("sunset")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(Decimal(str(response.json()["total_revenue"])), Decimal("2250.00"))


if __name__ == "__main__":
    unittest.main()
