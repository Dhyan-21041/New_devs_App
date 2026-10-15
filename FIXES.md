# Revenue dashboard fixes and verification

## Reporting contract

- A reservation's full amount belongs to its check-in month in the property's IANA timezone.
- Months use inclusive local midnight at the start and exclusive local midnight at the next month.
- Each boundary is independently converted to UTC, including daylight-saving changes.
- PostgreSQL sums exact NUMERIC amounts before Decimal half-up rounding once to two places.
- The API returns money as a fixed two-decimal string.
- The UI formats that string without converting it to a JavaScript number.
- A period containing multiple currencies returns HTTP 422 rather than adding unlike amounts.
- A single-currency period preserves its currency; an empty period returns zero with the existing USD default.

The dashboard initially selects March 2024 because the supplied reservations are for that month.
The reporting-month control can select other months and years.

## Fixes in severity order

### 1. Tenant isolation and real database access

Commit: `16095cc0c`.

The revenue cache was keyed only by property ID, although both tenants own distinct properties named `prop-001`.
Cached results bypassed database tenant filtering.
The property selector also contained a shared hardcoded list, and the API did not independently check ownership before reading the cache.

Changes:

- Check property ownership using both tenant and property ID before every cache lookup.
- Scope cache entries to the authenticated tenant and validate cached response identity.
- Use a new cache namespace so old unsafe entries are never read.
- Load the property selector from a tenant-filtered database endpoint.
- Reject foreign and nonexistent properties with HTTP 404 and missing tenant context with HTTP 403.
- Obtain tenant identity from backend authentication, never a simulation header.
- Reset dashboard state on account changes and ignore obsolete component requests.

The SQLAlchemy pool also referenced nonexistent Supabase database settings, used an incompatible pool class, and returned a session coroutine incorrectly.
The calculator hid those failures by returning hardcoded totals with HTTP 200.

- Use DATABASE_URL with SQLAlchemy's asyncpg driver and shared application-lifecycle pool.
- Return real PostgreSQL aggregates and explicit HTTP 503 errors on database failure.
- Remove the fabricated revenue fallback.
- Declare the async SQLAlchemy, asyncpg, and PyJWT dependencies in the backend manifests.
- Treat Redis as an optional optimization: cache failures fall back to real database calculation.

### 2. Property-local monthly reporting and exact money

Commit: `ea9fde792`.

The dashboard previously called an all-time calculator; the monthly calculator was an unused placeholder.
The API then converted money to float, and the browser applied binary-float rounding.

- Implement the property-local check-in boundaries described above.
- Add validated month/year parameters and the reporting-month control.
- Include tenant, property, timezone, month, and year in cache identity.
- Preserve exact decimal totals through the API and UI.
- Reject mixed-currency totals and preserve an actual single currency.
- Remove the fabricated 12% trend indicator.

Requests supplying neither month nor year retain the existing all-time API behavior.
Supplying only one parameter returns HTTP 422.

### 3. Session stability and frontend cache isolation

Commit: `2fdffd918`.

- Align local-auth responses with the active auth provider's `{data, error}` contract while preserving legacy top-level accessors.
- Carry tenant metadata and JWT expiry into the stored session.
- Decode base64url and UTF-8 JWT metadata correctly.
- Preserve sessions during temporary network/server outages; clear rejected tokens on HTTP 401.
- Prevent a delayed validation response from clearing a newer account's session.
- Revalidate fixed-lifetime challenge JWTs through the compatibility refresh method.
- Preserve the local-auth token during storage migration.
- Stop treating the plain-text migration version marker as corrupt JSON.
- Guard absent Supabase configuration during legacy recovery and persist the complete session.
- Avoid duplicate session-monitor intervals and clean up activity listeners.
- Scope request caches to opaque tenant IDs and full user identity.
- Reject requests whose account changes while they are pending.
- Replace broken asynchronous-storage/synchronous-persister wiring with account-scoped in-memory query caches.
- Only hydrate bootstrap data matching both the signed-in tenant and user.
- Remount application context on account changes to prevent stale context rendering.

## Verified seeded results

All amounts below are USD for March 2024.

| Account | Property | Revenue | Bookings |
| --- | --- | ---: | ---: |
| Sunset | Beach House Alpha | 2,250.00 | 4 |
| Sunset | City Apartment Downtown | 4,975.50 | 4 |
| Sunset | Country Villa Estate | 6,100.50 | 2 |
| Ocean | Mountain Lodge Beta | 0.00 | 0 |
| Ocean | Lakeside Cottage | 1,776.50 | 4 |
| Ocean | Urban Loft Modern | 3,256.00 | 3 |

Beach House Alpha's UTC `2024-02-29 23:30` check-in is March 1 in Paris and contributes 1,250.00 to March.
Its three fractional reservations sum exactly to 1,000.00, producing the 2,250.00 total.
The original displayed 1,000.00 was a hardcoded database-error fallback, not proof that the dashboard executed UTC-month filtering.

## Automated checks

### Backend

Use a PostgreSQL test database initialized with `database/schema.sql` and `database/seed.sql`.
From `backend/`, after installing requirements:

```bash
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5433/propertyflow \
REDIS_URL=redis://127.0.0.1:6380/0 \
.venv/bin/python -m unittest discover -s tests -v
```

Verified: 13 tests pass.
They use real PostgreSQL queries and an isolated in-memory Redis substitute for deterministic cache checks.
Additional financial/date fixtures run inside rolled-back transactions and do not alter the seed data.

Coverage includes tenant isolation in both request orders, warm/cold unauthorized access, missing authentication/tenant, database failure, corrupt cache identity, Redis failure, month cache separation, Paris/New York DST boundaries, December rollover, exact fractional aggregation, positive/negative half-up ties, currency behavior, and invalid periods.
Measured statement coverage across `dashboard.py`, `database_pool.py`, `cache.py`, and `reservations.py`: **84%**.
Targeted Ruff checks on those modules and their regression tests pass.

### Frontend

From `frontend/`:

```bash
npm test
npm run build
node node_modules/eslint/bin/eslint.js src/components/Dashboard.tsx src/components/RevenueSummary.tsx
```

Verified: 9 tests pass, the Vite production build succeeds, and the two changed dashboard components pass ESLint.
The tests use Node's test runner and Vite's module loader without adding a testing framework.

Broader legacy frontend files still contain pre-existing ESLint violations, including explicit-any annotations and an unused `PageNotFound` function in App.tsx.
The targeted lint result is not a repository-wide lint pass.

## Browser and live-service checks

- Logged in with both supplied accounts and exercised all six property selections.
- Switched from Sunset's Country Villa Estate to Ocean and verified Mountain Lodge Beta with zero revenue and no Sunset options.
- Refreshed repeatedly without losing the signed-in account or exposing another tenant's totals.
- Switched reporting months and verified February zero versus March 2,250.00 for Sunset prop-001.
- Exercised the live API and Redis in A/B/B/A order for shared property ID prop-001.
- Confirmed foreign properties return HTTP 404 even after the owner warms their cache.
- Sent conflicting simulation headers and verified authenticated tenant isolation remains intact.
- Simulated a delayed frontend response during an account change and confirmed `TenantIsolationError` rather than accepting stale data.
- Inspected the desktop layout and the 375px mobile layout; the mobile document width matches its viewport.
- Repeated-refresh dashboard checks had no application console errors.
- Deliberate forbidden-property API probes naturally produce expected HTTP 404 entries in browser developer tools.

Local verification used native PostgreSQL, Redis, Uvicorn, and Vite because Docker was unavailable on the development machine.

## Loom outline: approximately 7 minutes

1. **0:00-0:45:** Explain the three reported symptoms and show the original failure evidence.
2. **0:45-2:00:** Show the shared property ID, unsafe original cache key, ownership checks, and tenant-filtered property lists.
3. **2:00-3:00:** Explain broken pool settings and the removed hardcoded success fallback.
4. **3:00-4:15:** Show the Paris boundary reservation and change the reporting month in the UI.
5. **4:15-5:00:** Explain exact aggregation, final half-up rounding, and the decimal-string API.
6. **5:00-6:00:** Switch accounts, refresh, and show that Ocean's prop-001 remains zero with its own property name.
7. **6:00-7:00:** Run the backend/frontend tests, mention the production build, and summarize the phased commits.
