# Property Revenue Dashboard: Investigation and Fixes

This repository contains fixes for the [Property Revenue Dashboard debugging assignment](ASSIGNMENT.md).
The investigation addressed incorrect monthly revenue, cross-tenant data exposure, and cents-level rounding errors, followed by secondary issues affecting login, refresh, and account switching.

The existing React/Vite, FastAPI, PostgreSQL, and Redis architecture was retained.

## Resolution summary

**The three reported revenue/privacy issues and the secondary session/cache issues listed below have been fixed and verified.**
Verification includes both supplied client accounts, automated regression tests, browser interactions, and live PostgreSQL/Redis checks.
This is a scoped dashboard repair, not a claim that every legacy module in the repository is defect-free.

| Area | Status | Result |
| --- | --- | --- |
| Cross-tenant revenue exposure | Fixed | Tenant-scoped cache keys, ownership checks before cache access, and cached identity validation |
| Incorrect property lists | Fixed | Properties loaded from PostgreSQL for the authenticated tenant |
| Broken database queries and fabricated totals | Fixed | Shared async database pool, real aggregates, and explicit failure responses |
| Incorrect monthly reporting | Fixed | Selectable reporting month with property-local check-in boundaries |
| Financial rounding | Fixed | Exact aggregation, final half-up rounding, and decimal-string transport/display |
| Login, refresh, and account-switch instability | Fixed | Correct auth contracts, preserved sessions, and account-scoped frontend state |

## A. Primary critical issues

### 1. Cross-tenant cache leakage and incorrect property access

**Issue noticed:** Revenue could appear to belong to another company, and both accounts saw the same hardcoded property list.

**Root cause:** The original Redis key was `revenue:{property_id}`, even though property identity is the composite `(id, tenant_id)`.
Both clients legitimately own separate properties with ID `prop-001`.
A cache hit bypassed database tenant filtering, and the API did not independently verify property ownership first.
The frontend's shared list also exposed other tenants' property names and omitted Ocean's Mountain Lodge Beta.

**Fixes:**

- Check ownership using the authenticated tenant and property ID before reading the cache.
- Scope cache entries to tenant, property, timezone, and reporting period.
- Validate tenant/property/period identity when reading cached data.
- Use a new cache namespace so older unsafe entries are ignored.
- Replace the hardcoded selector with a tenant-filtered property endpoint.
- Return HTTP 404 for foreign/nonexistent properties and HTTP 403 for missing tenant context.
- Reject obsolete frontend responses after an account change.

Main files: [cache.py](backend/app/services/cache.py), [dashboard.py](backend/app/api/v1/dashboard.py), [Dashboard.tsx](frontend/src/components/Dashboard.tsx).

### 2. Broken database access hidden by fabricated success responses

**Issue noticed:** Sunset's Beach House Alpha showed `1,000.00` and 3 bookings instead of its actual seeded total and count.

**Root cause:** The database pool referenced nonexistent Supabase settings, selected an incompatible pool class, and exposed an async session getter incorrectly.
The calculator created a new pool for each request and caught failures by returning hardcoded property totals with HTTP 200.
Those fabricated totals could then be cached as legitimate revenue.

**Fixes:**

- Use `DATABASE_URL` with SQLAlchemy's asyncpg driver.
- Initialize and close a shared pool through the application's lifecycle.
- Correct session acquisition and declare the required async database dependencies.
- Remove the hardcoded revenue fallback and calculate from actual reservations.
- Return HTTP 503 when database access fails.
- If Redis fails, calculate from PostgreSQL rather than returning mock values.

Main files: [database_pool.py](backend/app/core/database_pool.py), [reservations.py](backend/app/services/reservations.py), [main.py](backend/app/main.py).

### 3. Missing property-local monthly reporting

**Issue noticed:** The dashboard described monthly performance but called an all-time calculator.
The separate monthly function was an unused placeholder that returned zero.

**Boundary example:** The seeded check-in at `2024-02-29 23:30 UTC` is `2024-03-01 00:30` in Paris.
Its `1,250.00` belongs to March under the agreed reporting rule.

**Fixes:**

- Attribute the full reservation amount to its property-local check-in month.
- Construct inclusive month-start and exclusive next-month-start boundaries in the property's IANA timezone.
- Convert each boundary independently to UTC, accounting for daylight-saving changes.
- Add validated month/year API parameters and a reporting-month control.
- Include the reporting period in cache identity.

The dashboard initially selects March 2024 to match the supplied dataset.
Requests omitting both month and year retain the existing all-time API behavior; supplying only one returns HTTP 422.

The original displayed `1,000.00` came from the fabricated database-error fallback.
It was not evidence that the running dashboard executed a UTC-month query.

### 4. Financial precision and currency handling

**Issue noticed:** The browser's original `Math.round(amount * 100) / 100` expression rounded `1.005` to `1.00` and `10.075` to `10.07`.
The API also converted decimal totals into binary floats.

**Fixes:**

- Sum PostgreSQL `NUMERIC` amounts exactly before rounding.
- Apply Decimal `ROUND_HALF_UP` once to the final aggregate.
- Return a fixed two-decimal string and format it without JavaScript floating-point arithmetic.
- Preserve the actual currency for single-currency periods.
- Return HTTP 422 for mixed-currency periods instead of combining unlike amounts.
- Remove the fabricated 12% revenue trend indicator.

For example, `333.333 + 333.333 + 333.334` produces `1,000.00` when rounded after aggregation, rather than the `999.99` obtained by rounding each row first.
The seeded aggregate totals did not themselves demonstrate a displayed cent error; the halfway inputs independently reproduced the original rounding defect.

Main files: [reservations.py](backend/app/services/reservations.py), [RevenueSummary.tsx](frontend/src/components/RevenueSummary.tsx).

## B. Secondary issues

| Issue noticed | Fix implemented |
| --- | --- |
| Local-auth response shape did not match the active auth provider | Added the expected `{data, error}` contract while preserving legacy accessors |
| Tenant metadata and session expiry were missing from local sessions | Populate tenant metadata and JWT expiry; decode base64url/UTF-8 claims correctly |
| Temporary network/server failures removed valid sessions | Preserve sessions during outages and clear rejected tokens on HTTP 401 |
| Delayed validation could log out a newer account | Check the session identity before applying an old validation response |
| Legacy migration deleted the local-auth token | Preserve `base360-auth-token` during migration |
| Storage health checks removed the plain-text version marker | Recognize the migration marker instead of treating it as corrupt JSON |
| Legacy recovery assumed Supabase configuration and lost session fields | Guard missing configuration and persist the complete session |
| Session monitoring could create duplicate timers/listeners | Make startup idempotent and remove activity listeners on cleanup |
| Frontend cache extraction expected UUID tenant IDs and missed app metadata | Support opaque tenant IDs and full authenticated user identity |
| A synchronous query persister was wired to asynchronous localForage | Replace the broken persistence wiring with account-scoped in-memory query caches |
| Bootstrap/context data could be reused across accounts | Match both tenant and user before hydration and reset context on account changes |
| Earlier requests could overwrite newer property/period results | Ignore obsolete component responses and reset displayed data/error state |

Local challenge JWTs have a fixed lifetime and no refresh-token endpoint.
The compatibility refresh method revalidates the existing token; it does not manufacture a new expiry.

## Verified March 2024 results

| Client | Property | Revenue (USD) | Bookings |
| --- | --- | ---: | ---: |
| Sunset Properties | Beach House Alpha | 2,250.00 | 4 |
| Sunset Properties | City Apartment Downtown | 4,975.50 | 4 |
| Sunset Properties | Country Villa Estate | 6,100.50 | 2 |
| Ocean Rentals | Mountain Lodge Beta | 0.00 | 0 |
| Ocean Rentals | Lakeside Cottage | 1,776.50 | 4 |
| Ocean Rentals | Urban Loft Modern | 3,256.00 | 3 |

Each account sees only its own three properties.
Sunset and Ocean's separate `prop-001` records remain isolated on cache hits, refreshes, and account switches.

## Running the application

The assignment's Docker Compose entry point, from the repository root, is:

```bash
docker compose up --build
```

- Frontend: <http://localhost:3000>
- Backend API documentation: <http://localhost:8000/docs>

The [supplied challenge credentials](ASSIGNMENT.md#client-access-credentials) are:

| Client | Email | Password |
| --- | --- | --- |
| Sunset Properties | `sunset@propertyflow.com` | `client_a_2024` |
| Ocean Rentals | `ocean@propertyflow.com` | `client_b_2024` |

Verification was performed using native PostgreSQL, Redis, Uvicorn, and Vite because Docker was unavailable on the development machine.
The Docker build itself was not verified locally.

## Tests and verification

### Backend regression tests

Use a PostgreSQL test database initialized with [schema.sql](database/schema.sql) and [seed.sql](database/seed.sql), with backend requirements installed in `.venv`.
Run from `backend/`:

```bash
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5433/propertyflow \
REDIS_URL=redis://127.0.0.1:6380/0 \
.venv/bin/python -m unittest discover -s tests -v
```

**Result: 13 tests pass.**
Tests execute real PostgreSQL queries and use an isolated Redis substitute for deterministic cache behavior.
Additional date/financial fixtures are rolled back after each test.
Coverage includes tenant isolation in both request orders, ownership on warm/cold caches, missing authentication/tenant, database/Redis failure, period separation, DST/year boundaries, half-up rounding, exact aggregation, and currency validation.

### Frontend tests and production build

Run from `frontend/`:

```bash
npm test
npm run build
```

**Results: 9 tests pass; the Vite production build passes.**
Frontend tests cover auth response compatibility, outages, rejected tokens, delayed account validation, JWT decoding, and storage migration/health-check behavior.

Additional verification:

- 84% measured statement coverage across the four core backend revenue modules.
- Targeted Ruff checks pass for the changed backend revenue modules and regression tests.
- Targeted ESLint checks pass for `Dashboard.tsx` and `RevenueSummary.tsx`.
- Browser checks exercised all six properties, month changes, account switching, and repeated refreshes.
- Live API/Redis checks confirmed isolation in both account orders and rejection of foreign properties.
- A conflicting simulation header could not change the authenticated tenant.
- A simulated delayed response during an account change was rejected.
- Desktop and 375px mobile layouts were inspected; the mobile layout had no horizontal overflow.

## Scope and remaining limitations

- The fixes above cover the reported dashboard problems and the secondary auth/session/cache defects encountered in that flow.
- Broader legacy frontend files still contain existing lint violations, including explicit-any annotations and an unused `PageNotFound` function.
- Some auxiliary Supabase-based routes and legacy health checks use mock/legacy infrastructure in challenge mode and can still report degraded status.
- Unused legacy stubs and auxiliary modules were not comprehensively repaired or regression-tested.
- Mixed-currency conversion is not implemented; mixed totals are explicitly rejected, and empty periods retain the existing USD default.
- Verification demonstrates the supplied challenge scenarios, not a complete production-readiness or repository-wide security audit.

## Commit history and detailed notes

| Commit | Phase |
| --- | --- |
| `16095cc0c` | Critical tenant isolation and database access |
| `ea9fde792` | Property-local reporting and exact financial rounding |
| `2fdffd918` | Secondary session stability and frontend cache isolation |
| `44c29253c` | Verification, backend lint/type cleanup, and walkthrough notes |

See [FIXES.md](FIXES.md) for additional verification details and a suggested 7-minute Loom outline.
The assignment also requires a separate 5-10 minute Loom recording.
