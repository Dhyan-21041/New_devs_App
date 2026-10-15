"""Dashboard regressions against the supplied PostgreSQL schema and seed data.

Run from backend/: python -m unittest discover -s tests -v
Set DATABASE_URL to a disposable database loaded with database/{schema,seed}.sql.
"""

import json
import unittest
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

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

    async def summary(self, account, property_id="prop-001", **period):
        return await self.client.get(
            "/api/v1/dashboard/summary",
            params={"property_id": property_id, **period}, headers=self.headers[account],
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

    @asynccontextmanager
    async def fixture_reservations(self, timezone, rows):
        """Uncommitted fixtures are visible only to requests in this test."""
        async with db_pool.engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text("""
                    INSERT INTO properties (id, tenant_id, name, timezone)
                    VALUES ('test-period', 'tenant-b', 'Period fixture', :timezone)
                """), {"timezone": timezone})
                for index, (check_in, amount, currency) in enumerate(rows):
                    await connection.execute(text("""
                        INSERT INTO reservations
                            (id, property_id, tenant_id, check_in_date, check_out_date, total_amount, currency)
                        VALUES (:id, 'test-period', 'tenant-b', CAST(:date AS timestamptz),
                                CAST(:date AS timestamptz) + interval '1 day', :amount, :currency)
                    """), {"id": f"test-period-{index}", "date": datetime.fromisoformat(check_in),
                           "amount": Decimal(amount), "currency": currency})
                with patch.object(db_pool, "session_factory", async_sessionmaker(
                    bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
                )):
                    yield
            finally:
                await transaction.rollback()

    async def test_march_seed_and_period_cache_separation(self):
        for month, expected, count in ((3, "2250.00", 4), (2, "0.00", 0), (3, "2250.00", 4)):
            response = await self.summary("sunset", month=month, year=2024)
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertEqual(data["total_revenue"], expected)
            self.assertEqual(data["reservations_count"], count)
            self.assertEqual(data["month"], month)
            self.assertEqual(data["timezone"], "Europe/Paris")

    async def test_local_month_boundaries_and_dst(self):
        cases = (
            ("Europe/Paris", 3, 2024, "2024-02-29T22:59:59Z", "2024-02-29T23:00:00Z",
             "2024-03-31T21:59:59Z", "2024-03-31T22:00:00Z"),
            ("America/New_York", 3, 2024, "2024-03-01T04:59:59Z", "2024-03-01T05:00:00Z",
             "2024-04-01T03:59:59Z", "2024-04-01T04:00:00Z"),
            ("America/New_York", 11, 2024, "2024-11-01T03:59:59Z", "2024-11-01T04:00:00Z",
             "2024-12-01T04:59:59Z", "2024-12-01T05:00:00Z"),
            ("Europe/Paris", 12, 2024, "2024-11-30T22:59:59Z", "2024-11-30T23:00:00Z",
             "2024-12-31T22:59:59Z", "2024-12-31T23:00:00Z"),
        )
        for timezone, month, year, before, start, last, end in cases:
            with self.subTest(timezone=timezone, month=month):
                self.cache.values.clear()
                rows = [(before, "100", "USD"), (start, "1.005", "USD"),
                        (last, "2.005", "USD"), (end, "1000", "USD")]
                async with self.fixture_reservations(timezone, rows):
                    response = await self.summary("ocean", "test-period", month=month, year=year)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["total_revenue"], "3.01")
                    self.assertEqual(response.json()["reservations_count"], 2)

    async def test_half_up_rounding_and_aggregate_once(self):
        for amounts, expected in (
            (["1.005"], "1.01"), (["10.075"], "10.08"), (["-1.005"], "-1.01"),
            (["333.333", "333.333", "333.334"], "1000.00"),
        ):
            self.cache.values.clear()
            rows = [("2024-03-15T12:00:00Z", amount, "USD") for amount in amounts]
            async with self.fixture_reservations("Europe/Paris", rows):
                response = await self.summary("ocean", "test-period", month=3, year=2024)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["total_revenue"], expected)

    async def test_different_currencies_are_not_added_together(self):
        rows = [("2024-03-15T12:00:00Z", "10", "USD"),
                ("2024-03-16T12:00:00Z", "20", "EUR")]
        async with self.fixture_reservations("Europe/Paris", rows):
            response = await self.summary("ocean", "test-period", month=3, year=2024)
            self.assertEqual(response.status_code, 422)
            self.assertFalse(self.cache.values)

    async def test_single_currency_is_preserved(self):
        async with self.fixture_reservations("Europe/Paris", [("2024-03-15T12:00:00Z", "10", "EUR")]):
            response = await self.summary("ocean", "test-period", month=3, year=2024)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["currency"], "EUR")

    async def test_invalid_or_incomplete_period_rejected(self):
        for params in ({"month": 0, "year": 2024}, {"month": 13, "year": 2024},
                       {"month": 3}, {"year": 2024}, {"month": 3, "year": 10000}):
            response = await self.summary("sunset", **params)
            self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
