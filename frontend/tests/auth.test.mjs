import assert from 'node:assert/strict';
import { after, beforeEach, test } from 'node:test';
import { createServer } from 'vite';

const values = new Map();
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, value),
  removeItem: (key) => values.delete(key),
  key: (index) => [...values.keys()][index] ?? null,
  clear: () => values.clear(),
  get length() { return values.size; },
};
const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
const { localAuthClient } = await server.ssrLoadModule('/src/lib/localAuthClient.ts');
const { decodeJWTPayload } = await server.ssrLoadModule('/src/utils/jwtUtils.ts');
const originalFetch = globalThis.fetch;
after(async () => {
  globalThis.fetch = originalFetch;
  await server.close();
});

const user = { id: 'user-sunset', email: 'sunset@propertyflow.com', tenant_id: 'tenant-a' };
const token = `e30.${Buffer.from(JSON.stringify({ exp: 4102444800, app_metadata: { tenant_id: 'tenant-a' } })).toString('base64url')}.signature`;
const session = { access_token: token, token_type: 'bearer', user };

beforeEach(async () => {
  values.clear();
  await localAuthClient.setSession(session);
});

test('login response matches the active auth provider contract', async () => {
  globalThis.fetch = async () => Response.json({ access_token: token, user });
  const result = await localAuthClient.signInWithPassword({ email: user.email, password: 'fixture' });
  assert.equal(result.error, null);
  assert.equal(result.data.session.user.email, user.email);
  assert.equal(result.data.session.expires_at, 4102444800);
  assert.equal(result.data.session.user.app_metadata.tenant_id, 'tenant-a');
});

test('invalid credentials return a structured error without a session', async () => {
  globalThis.fetch = async () => Response.json({ detail: 'Invalid credentials' }, { status: 401 });
  const result = await localAuthClient.signInWithPassword({ email: user.email, password: 'wrong' });
  assert.equal(result.data.session, null);
  assert.equal(result.error.message, 'Invalid credentials');
});

test('temporary server failure preserves the stored session', async () => {
  globalThis.fetch = async () => new Response('', { status: 503 });
  await localAuthClient.getSession();
  assert.ok(localStorage.getItem('base360-auth-token'));
  globalThis.fetch = async () => Response.json(user);
  assert.equal((await localAuthClient.getSession()).data.session.user.id, user.id);
});

test('temporary network failure preserves the stored session', async () => {
  globalThis.fetch = async () => { throw new TypeError('Network unavailable'); };
  await localAuthClient.getSession();
  assert.ok(localStorage.getItem('base360-auth-token'));
});

test('a rejected token clears the session', async () => {
  globalThis.fetch = async () => new Response('', { status: 401 });
  assert.equal((await localAuthClient.getSession()).data.session, null);
  assert.equal(localStorage.getItem('base360-auth-token'), null);
});

test('a delayed response cannot clear a newer account session', async () => {
  let finish;
  globalThis.fetch = () => new Promise((resolve) => { finish = resolve; });
  const pending = localAuthClient.getSession();
  await localAuthClient.setSession({ ...session, user: { ...user, id: 'user-ocean' } });
  finish(new Response('', { status: 401 }));
  await pending;
  assert.equal(JSON.parse(localStorage.getItem('base360-auth-token')).user.id, 'user-ocean');
});

test('getUser and refreshSession expose the expected compatibility contract', async () => {
  globalThis.fetch = async () => Response.json(user);
  assert.equal((await localAuthClient.auth.getUser()).data.user.id, user.id);
  assert.equal((await localAuthClient.auth.refreshSession()).data.session.user.id, user.id);
});

test('JWT metadata decodes base64url and unicode', () => {
  const claims = { name: 'Résumé 🏡', tenant_id: 'tenant-a' };
  const payload = Buffer.from(JSON.stringify(claims)).toString('base64url');
  assert.deepEqual(decodeJWTPayload(`e30.${payload}.signature`), claims);
});

test('storage migration and health checks preserve local auth and its version marker', async () => {
  globalThis.fetch = async () => Response.json(user);
  const interval = globalThis.setInterval;
  const timeout = globalThis.setTimeout;
  const timers = [];
  globalThis.setInterval = (...args) => { const id = interval(...args); timers.push(id); return id; };
  globalThis.setTimeout = (...args) => { const id = timeout(...args); timers.push(id); return id; };
  try {
    const { storageHealthChecker } = await server.ssrLoadModule('/src/utils/localStorageManager.ts');
    assert.ok(localStorage.getItem('base360-auth-token'));
    const report = await storageHealthChecker.performHealthCheck();
    assert.ok(!report.issues.some(issue => issue.affected_keys.includes('app_storage_version')));
    assert.equal(localStorage.getItem('app_storage_version'), '2.0.0');
  } finally {
    timers.forEach(clearTimeout);
    globalThis.setInterval = interval;
    globalThis.setTimeout = timeout;
  }
});
