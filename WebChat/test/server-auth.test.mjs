import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';

import {
  hashPassword,
  verifyPassword,
  createSession,
  deleteSession,
  getSessionUser,
  createAuthMiddleware,
  SESSION_COOKIE_NAME,
} from '../dist-server/auth.js';

import { createApp } from '../dist-server/index.js';

class MockPool {
  constructor() {
    this.users = new Map();
    this.sessions = new Map();
    this.conversations = new Map();
    this.messages = [];
  }

  async connect() {
    return {
      query: (sql, params) => this.query(sql, params),
      release: () => {},
    };
  }

  async query(sql, params = []) {
    const s = sql.replace(/\s+/g, ' ').trim();

    if (['BEGIN', 'COMMIT', 'ROLLBACK'].includes(s) || s.startsWith('SET LOCAL') || s.includes('pg_advisory_xact_lock')) return { rows: [] };
    if (s === 'SELECT role FROM users WHERE id = $1') {
      const user = this.users.get(params[0]);
      return { rows: user ? [{ role: user.role }] : [] };
    }
    if (s.includes("COUNT(*)::int AS count FROM users WHERE role = 'admin'")) {
      return { rows: [{ count: [...this.users.values()].filter(u => u.role === 'admin').length }] };
    }
    if (s.startsWith('UPDATE users SET role')) {
      const user = this.users.get(params[2]);
      Object.assign(user, { role: params[0], departments: params[1] });
      return { rows: [{ id: user.id, username: user.username, role: user.role, departments: user.departments }] };
    }
    if (s.includes('strpos(lower(username)')) {
      const users = [...this.users.values()].filter(u => u.username.toLowerCase().includes(params[0].toLowerCase()));
      if (s.startsWith('SELECT COUNT')) return { rows: [{ count: users.length }] };
      return { rows: users.slice(params[1], params[1] + 20).map(({ password_hash, ...user }) => user) };
    }
    if (s.includes('(SELECT COUNT(*)::int FROM users) AS users')) {
      return { rows: [{ users: this.users.size, conversations: this.conversations.size, messages: this.messages.length, sessions: this.sessions.size }] };
    }

    if (s.startsWith('SELECT 1')) {
      return { rowCount: 1, rows: [{ '?column?': 1 }] };
    }

    if (s.includes('SELECT id FROM users WHERE username')) {
      const username = params[0];
      const match = [...this.users.values()].find(u => u.username === username);
      return { rowCount: match ? 1 : 0, rows: match ? [{ id: match.id }] : [] };
    }

    if (s.startsWith('INSERT INTO users')) {
      const [username, password_hash, role, departments] = params;
      const id = 'user-' + Math.random().toString(36).slice(2, 10);
      const user = {
        id,
        username,
        password_hash,
        role: role || 'employee',
        departments: departments || ['general'],
        created_at: new Date().toISOString(),
      };
      this.users.set(id, user);
      return { rowCount: 1, rows: [{ id, username, role: user.role, departments: user.departments }] };
    }

    if (s.includes('FROM users WHERE username')) {
      const username = params[0];
      const match = [...this.users.values()].find(u => u.username === username);
      return {
        rowCount: match ? 1 : 0,
        rows: match
          ? [{
              id: match.id,
              username: match.username,
              password_hash: match.password_hash,
              role: match.role || 'employee',
              departments: match.departments || ['general'],
            }]
          : [],
      };
    }

    if (s.startsWith('INSERT INTO sessions')) {
      const [id, user_id, expires_at, user_agent, ip_address] = params;
      this.sessions.set(id, { id, user_id, expires_at, user_agent, ip_address });
      return { rowCount: 1, rows: [] };
    }

    if (s.startsWith('DELETE FROM sessions WHERE id = $1')) {
      const id = params[0];
      const existed = this.sessions.delete(id);
      return { rowCount: existed ? 1 : 0, rows: [] };
    }

    if (s.includes('FROM sessions s') && s.includes('JOIN users u')) {
      const sessionId = params[0];
      const sess = this.sessions.get(sessionId);
      if (!sess) return { rowCount: 0, rows: [] };
      if (new Date(sess.expires_at) < new Date()) return { rowCount: 0, rows: [] };
      const user = this.users.get(sess.user_id);
      if (!user) return { rowCount: 0, rows: [] };
      return {
        rowCount: 1,
        rows: [{
          id: user.id,
          username: user.username,
          role: user.role || 'employee',
          departments: user.departments || ['general'],
        }],
      };
    }

    if (s.includes('FROM conversations') && s.includes('WHERE user_id = $1')) {
      const userId = params[0];
      const list = [...this.conversations.values()]
        .filter(c => c.user_id === userId)
        .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
      return { rowCount: list.length, rows: list };
    }

    if (s.startsWith('INSERT INTO conversations')) {
      const [user_id, title, agent_thread_id] = params;
      const id = 'conv-' + Math.random().toString(36).slice(2, 10);
      const conv = {
        id,
        user_id,
        title,
        agent_thread_id,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      this.conversations.set(id, conv);
      return { rowCount: 1, rows: [conv] };
    }

    if (s.startsWith('SELECT') && s.includes('FROM conversations') && s.includes('WHERE id = $1 AND user_id = $2')) {
      const [id, userId] = params;
      const conv = this.conversations.get(id);
      if (conv && conv.user_id === userId) {
        return { rowCount: 1, rows: [conv] };
      }
      return { rowCount: 0, rows: [] };
    }

    if (s.includes('DELETE FROM conversations') && s.includes('WHERE id = $1 AND user_id = $2')) {
      const [id, userId] = params;
      const conv = this.conversations.get(id);
      if (conv && conv.user_id === userId) {
        this.conversations.delete(id);
        this.messages = this.messages.filter(m => m.conversation_id !== id);
        return { rowCount: 1, rows: [{ id }] };
      }
      return { rowCount: 0, rows: [] };
    }

    if (s.includes('FROM messages') && s.includes('WHERE conversation_id = $1')) {
      const conversationId = params[0];
      const msgs = this.messages.filter(m => m.conversation_id === conversationId);
      return { rowCount: msgs.length, rows: msgs };
    }

    if (s.startsWith('INSERT INTO messages')) {
      const [conversation_id, sender, text, attachments] = params;
      const id = 'msg-' + Math.random().toString(36).slice(2, 10);
      const msg = {
        id,
        conversation_id,
        sender,
        text,
        attachments: typeof attachments === 'string' ? JSON.parse(attachments) : (attachments || []),
        created_at: new Date().toISOString(),
      };
      this.messages.push(msg);
      return { rowCount: 1, rows: [{ id, created_at: msg.created_at }] };
    }

    if (s.startsWith('UPDATE conversations SET updated_at')) {
      const convId = params[0];
      const conv = this.conversations.get(convId);
      if (conv) conv.updated_at = new Date().toISOString();
      return { rowCount: conv ? 1 : 0, rows: [] };
    }

    return { rowCount: 0, rows: [] };
  }
}

/**
 * Запуск тестового сервера Express через Named Pipe (IPC).
 * Это позволяет запускать тесты в любых песочницах без потребности в сетевых TCP портах.
 */
async function startServer(app) {
  const pipe = `\\\\.\\pipe\\webchat-test-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const server = app.listen(pipe);
  await once(server, 'listening');

  const request = (path, options = {}) => {
    return new Promise((resolve, reject) => {
      const headers = { ...(options.headers || {}) };
      let bodyData = null;

      if (options.body) {
        bodyData = typeof options.body === 'string' ? options.body : JSON.stringify(options.body);
        if (!headers['Content-Length'] && !headers['content-length']) {
          headers['Content-Length'] = Buffer.byteLength(bodyData);
        }
      }

      const req = http.request(
        {
          socketPath: pipe,
          path,
          method: options.method || 'GET',
          headers,
        },
        res => {
          const chunks = [];
          res.on('data', c => chunks.push(c));
          res.on('end', () => {
            const buffer = Buffer.concat(chunks);
            const text = buffer.toString('utf-8');
            resolve({
              status: res.statusCode,
              headers: {
                get(name) {
                  const val = res.headers[name.toLowerCase()];
                  return Array.isArray(val) ? val.join(', ') : val;
                },
              },
              json: async () => JSON.parse(text),
              text: async () => text,
            });
          });
        }
      );

      req.on('error', reject);
      if (bodyData) req.write(bodyData);
      req.end();
    });
  };

  return {
    request,
    close: async () => {
      server.closeAllConnections();
      await new Promise(res => server.close(res));
    },
  };
}

test('Password hashing and verification with bcryptjs', async () => {
  const plainPassword = 'SuperSecretPassword123!';
  const hash = await hashPassword(plainPassword);

  assert.ok(hash, 'Hash should not be empty');
  assert.notEqual(hash, plainPassword, 'Hash should differ from plaintext');
  assert.match(hash, /^\$2[aby]\$/, 'Hash should follow bcrypt format');

  const matches = await verifyPassword(plainPassword, hash);
  assert.equal(matches, true, 'Correct password should verify successfully');

  const fails = await verifyPassword('WrongPassword', hash);
  assert.equal(fails, false, 'Incorrect password should fail verification');
});

async function adminFixture(role = 'admin', fetchFn = async () => ({ ok: true, json: async () => ({ status: 'healthy', model: 'test-model', documents_count: 3 }) })) {
  const pool = new MockPool();
  const id = '00000000-0000-0000-0000-000000000001';
  pool.users.set(id, { id, username: 'admin-test', role, departments: ['general'] });
  const sid = await createSession(pool, id);
  const server = await startServer(createApp({ pool, fetchFn }));
  return { ...server, pool, id, cookie: `${SESSION_COOKIE_NAME}=${sid}` };
}

test('Admin API rejects anonymous and non-admin users on every route', async () => {
  const fixture = await adminFixture('employee');
  try {
    for (const [method, path] of [['GET', '/overview'], ['GET', '/users'], ['PATCH', `/users/${fixture.id}`], ['POST', '/documents']]) {
      assert.equal((await fixture.request(`/api/admin${path}`, { method })).status, 401);
      assert.equal((await fixture.request(`/api/admin${path}`, { method, headers: { Cookie: fixture.cookie } })).status, 403);
    }
  } finally { await fixture.close(); }
});

test('Admin overview and user search expose counts and safe user fields', async () => {
  const fixture = await adminFixture();
  try {
    const overview = await fixture.request('/api/admin/overview', { headers: { Cookie: fixture.cookie } });
    assert.equal(overview.status, 200);
    const status = await overview.json();
    assert.equal(status.counts.users, 1);
    assert.equal(status.agent.healthy, true);
    assert.equal(status.agent.chunks, 3);
    fixture.pool.users.get(fixture.id).password_hash = 'must-not-leak';
    const users = await (await fixture.request('/api/admin/users?search=admin', { headers: { Cookie: fixture.cookie } })).json();
    assert.equal(users.total, 1);
    assert.equal(users.users[0].password_hash, undefined);
  } finally { await fixture.close(); }
});

test('Admin cannot demote the last administrator, but can update another user', async () => {
  const fixture = await adminFixture();
  const headers = { Cookie: fixture.cookie, 'Content-Type': 'application/json' };
  try {
    const denied = await fixture.request(`/api/admin/users/${fixture.id}`, { method: 'PATCH', headers, body: { role: 'employee', departments: ['general'] } });
    assert.equal(denied.status, 409);
    assert.equal(fixture.pool.users.get(fixture.id).role, 'admin');
    const target = '00000000-0000-0000-0000-000000000002';
    fixture.pool.users.set(target, { id: target, username: 'employee-test', role: 'employee', departments: ['general'] });
    const updated = await fixture.request(`/api/admin/users/${target}`, { method: 'PATCH', headers, body: { role: 'manager', departments: ['finance', 'finance', ' it '] } });
    assert.equal(updated.status, 200);
    assert.deepEqual(fixture.pool.users.get(target).departments, ['finance', 'it']);
    assert.equal(fixture.pool.users.get(target).role, 'manager');
    const programmer = await fixture.request(`/api/admin/users/${target}`, { method: 'PATCH', headers, body: { role: 'programmer', departments: ['it'] } });
    assert.equal(programmer.status, 200);
    assert.equal(fixture.pool.users.get(target).role, 'programmer');
    assert.equal((await fixture.request(`/api/admin/users/${target}`, { method: 'PATCH', headers, body: { role: 'arbitrary', departments: [] } })).status, 400);
  } finally { await fixture.close(); }
});

test('Admin document upload forwards trusted owner and access, preserving the server token', async () => {
  let body, headers;
  const fixture = await adminFixture('admin', async (_url, options) => {
    body = JSON.parse(options.body); headers = options.headers;
    return { ok: true, json: async () => ({ document_id: 'doc-test', title: body.title, chunks_count: 2, status: 'indexed' }) };
  });
  try {
    const response = await fixture.request('/api/admin/documents', { method: 'POST', headers: { Cookie: fixture.cookie, 'Content-Type': 'application/json' },
      body: { title: ' Handbook ', content: ' Content ', owner_id: 'forged-owner', is_public: false, allowed_roles: [], allowed_departments: ['finance'] } });
    assert.equal(response.status, 201);
    assert.equal(body.owner_id, fixture.id);
    assert.equal(body.title, 'Handbook');
    assert.deepEqual(body.allowed_departments, ['finance']);
    assert.ok(headers['X-Internal-Token']);
  } finally { await fixture.close(); }
});

test('Unavailable agent is reported as unavailable without breaking the overview', async () => {
  const fixture = await adminFixture('admin', async () => { throw new Error('offline'); });
  try {
    const response = await fixture.request('/api/admin/overview', { headers: { Cookie: fixture.cookie } });
    assert.equal(response.status, 200);
    assert.equal((await response.json()).agent.healthy, false);
    const upload = await fixture.request('/api/admin/documents', { method: 'POST', headers: { Cookie: fixture.cookie, 'Content-Type': 'application/json' }, body: { title: 'Test', content: 'Body' } });
    assert.equal(upload.status, 502);
  } finally { await fixture.close(); }
});

test('Session creation, retrieval, and deletion in PostgreSQL sessions table', async () => {
  const pool = new MockPool();
  pool.users.set('test-user-1', {
    id: 'test-user-1',
    username: 'alice',
    password_hash: 'hash',
  });

  const sessionId = await createSession(pool, 'test-user-1', 'TestAgent/1.0', '127.0.0.1');
  assert.ok(sessionId, 'Session ID should be created');
  assert.equal(typeof sessionId, 'string');
  assert.equal(pool.sessions.size, 1);

  const user = await getSessionUser(pool, sessionId);
  assert.ok(user, 'User should be found for valid session');
  assert.equal(user.id, 'test-user-1');
  assert.equal(user.username, 'alice');

  // Несуществующая сессия
  const missing = await getSessionUser(pool, 'non-existent-session-id');
  assert.equal(missing, null);

  // Истекшая сессия
  const expiredSessionId = 'expired-sess-1';
  pool.sessions.set(expiredSessionId, {
    id: expiredSessionId,
    user_id: 'test-user-1',
    expires_at: new Date(Date.now() - 1000).toISOString(),
  });
  const expiredUser = await getSessionUser(pool, expiredSessionId);
  assert.equal(expiredUser, null, 'Expired session should return null');

  // Удаление сессии
  await deleteSession(pool, sessionId);
  const deletedUser = await getSessionUser(pool, sessionId);
  assert.equal(deletedUser, null, 'Deleted session should return null');
});

test('authMiddleware enforces session authentication', async () => {
  const pool = new MockPool();
  pool.users.set('user-bob', { id: 'user-bob', username: 'bob' });
  const validSessionId = await createSession(pool, 'user-bob');

  const mw = createAuthMiddleware(() => pool);

  // 1. Без cookie
  let status1 = 0;
  let json1 = null;
  const res1 = {
    status(s) { status1 = s; return this; },
    json(j) { json1 = j; },
  };
  await mw({ cookies: {} }, res1, () => {
    assert.fail('next should not be called when cookie missing');
  });
  assert.equal(status1, 401);
  assert.match(json1.detail, /Необходима авторизация/);

  // 2. С неверным cookie
  let status2 = 0;
  const res2 = {
    status(s) { status2 = s; return this; },
    json() {},
  };
  await mw({ cookies: { [SESSION_COOKIE_NAME]: 'invalid-token' } }, res2, () => {
    assert.fail('next should not be called with invalid token');
  });
  assert.equal(status2, 401);

  // 3. С валидным cookie
  let nextCalled = false;
  const req3 = { cookies: { [SESSION_COOKIE_NAME]: validSessionId } };
  await mw(req3, {}, () => {
    nextCalled = true;
  });
  assert.equal(nextCalled, true);
  assert.deepEqual(req3.user, {
    id: 'user-bob',
    username: 'bob',
    role: 'employee',
    departments: ['general'],
  });
});

test('HTTP API: /health returns status: ok and db status', async () => {
  const pool = new MockPool();
  const app = createApp({ pool });
  const { request, close } = await startServer(app);

  try {
    const res = await request('/health');
    assert.equal(res.status, 200);
    const data = await res.json();
    assert.equal(data.status, 'ok');
    assert.equal(data.db, true);
  } finally {
    await close();
  }
});

test('HTTP API: Full Authentication flow (Register -> Me -> Logout -> Login)', async () => {
  const pool = new MockPool();
  const app = createApp({ pool });
  const { request, close } = await startServer(app);

  try {
    // 1. Валидация при регистрации
    const badReg = await request('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'ab', password: '123' }),
    });
    assert.equal(badReg.status, 400);

    // 2. Успешная регистрация
    const regRes = await request('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'charlie', password: 'securePassword123' }),
    });
    assert.equal(regRes.status, 201);
    const regData = await regRes.json();
    assert.ok(regData.user.id);
    assert.equal(regData.user.username, 'charlie');

    const cookie = regRes.headers.get('set-cookie');
    assert.ok(cookie, 'Set-Cookie header should be present');
    assert.match(cookie, /session_id=/);

    // 3. Проверка текущего пользователя /api/auth/me
    const meRes = await request('/api/auth/me', {
      headers: { Cookie: cookie },
    });
    assert.equal(meRes.status, 200);
    const meData = await meRes.json();
    assert.equal(meData.user.username, 'charlie');

    // 4. Повторная регистрация того же имени возвращает 409
    const dupRes = await request('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'charlie', password: 'anotherPassword123' }),
    });
    assert.equal(dupRes.status, 409);

    // 5. Выход из системы
    const logoutRes = await request('/api/auth/logout', {
      method: 'POST',
      headers: { Cookie: cookie },
    });
    assert.equal(logoutRes.status, 200);
    const logoutData = await logoutRes.json();
    assert.equal(logoutData.ok, true);

    // /me теперь должен возвращать 401
    const meAfterLogout = await request('/api/auth/me', {
      headers: { Cookie: cookie },
    });
    assert.equal(meAfterLogout.status, 401);

    // 6. Авторизация с неверным паролем
    const badLogin = await request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'charlie', password: 'wrongPassword' }),
    });
    assert.equal(badLogin.status, 401);

    // 7. Успешная авторизация
    const loginRes = await request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'charlie', password: 'securePassword123' }),
    });
    assert.equal(loginRes.status, 200);
    const loginData = await loginRes.json();
    assert.equal(loginData.user.username, 'charlie');
  } finally {
    await close();
  }
});

test('HTTP API: Conversations CRUD and cascading messages', async () => {
  const pool = new MockPool();
  const app = createApp({ pool });
  const { request, close } = await startServer(app);

  try {
    // Регистрируем пользователя
    const regRes = await request('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'diana', password: 'dianaPassword123' }),
    });
    const cookie = regRes.headers.get('set-cookie');

    // Создаем диалог
    const createConvRes = await request('/api/conversations', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Cookie: cookie,
      },
      body: JSON.stringify({ title: 'Тестовый диалог' }),
    });
    assert.equal(createConvRes.status, 201);
    const convData = await createConvRes.json();
    const convId = convData.conversation.id;
    assert.ok(convId);
    assert.equal(convData.conversation.title, 'Тестовый диалог');
    assert.ok(convData.conversation.agent_thread_id);

    // Список диалогов
    const listRes = await request('/api/conversations', {
      headers: { Cookie: cookie },
    });
    assert.equal(listRes.status, 200);
    const listData = await listRes.json();
    assert.equal(listData.conversations.length, 1);
    assert.equal(listData.conversations[0].id, convId);

    // Получение деталей диалога
    const getRes = await request(`/api/conversations/${convId}`, {
      headers: { Cookie: cookie },
    });
    assert.equal(getRes.status, 200);
    const getData = await getRes.json();
    assert.equal(getData.conversation.id, convId);
    assert.deepEqual(getData.messages, []);

    // Удаление диалога
    const delRes = await request(`/api/conversations/${convId}`, {
      method: 'DELETE',
      headers: { Cookie: cookie },
    });
    assert.equal(delRes.status, 200);
    const delData = await delRes.json();
    assert.equal(delData.ok, true);

    // Повторное получение -> 404
    const getAfterDel = await request(`/api/conversations/${convId}`, {
      headers: { Cookie: cookie },
    });
    assert.equal(getAfterDel.status, 404);
  } finally {
    await close();
  }
});

test('HTTP API: Chat endpoint invokes APIAgent and saves messages', async () => {
  const pool = new MockPool();

  const mockFetch = async (_url, opts) => {
    const parsed = JSON.parse(opts.body);
    return {
      ok: true,
      status: 200,
      json: async () => ({
        reply: `Здравствуйте! Вы написали: ${parsed.message}`,
        thread_id: parsed.thread_id,
      }),
    };
  };

  const app = createApp({ pool, fetchFn: mockFetch });
  const { request, close } = await startServer(app);

  try {
    // 1. Регистрируем пользователя
    const regRes = await request('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'edward', password: 'edwardPassword123' }),
    });
    const cookie = regRes.headers.get('set-cookie');

    // 2. Создаем диалог
    const createConvRes = await request('/api/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Cookie: cookie },
      body: JSON.stringify({ title: 'Чат с агентом' }),
    });
    const { conversation } = await createConvRes.json();

    // 3. Отправляем сообщение через /api/chat
    const chatRes = await request('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Cookie: cookie },
      body: JSON.stringify({
        conversation_id: conversation.id,
        message: 'Привет, Агент!',
      }),
    });

    assert.equal(chatRes.status, 200);
    const chatData = await chatRes.json();
    assert.equal(chatData.reply, 'Здравствуйте! Вы написали: Привет, Агент!');
    assert.equal(chatData.thread_id, conversation.agent_thread_id);
    assert.ok(chatData.message_id);

    // 4. Проверяем историю сообщений диалога
    const convDetailsRes = await request(`/api/conversations/${conversation.id}`, {
      headers: { Cookie: cookie },
    });
    const convDetails = await convDetailsRes.json();
    assert.equal(convDetails.messages.length, 2);
    assert.equal(convDetails.messages[0].sender, 'user');
    assert.equal(convDetails.messages[0].text, 'Привет, Агент!');
    assert.equal(convDetails.messages[1].sender, 'assistant');
    assert.equal(convDetails.messages[1].text, 'Здравствуйте! Вы написали: Привет, Агент!');
  } finally {
    await close();
  }
});

test('HTTP API: Security: Passes X-Internal-Token and user_context to APIAgent', async () => {
  const pool = new MockPool();
  let capturedHeaders = null;
  let capturedBody = null;

  const mockFetch = async (_url, opts) => {
    capturedHeaders = opts.headers;
    capturedBody = JSON.parse(opts.body);
    return {
      ok: true,
      status: 200,
      json: async () => ({
        reply: 'Access granted',
        thread_id: capturedBody.thread_id,
      }),
    };
  };

  const app = createApp({ pool, fetchFn: mockFetch });
  const { request, close } = await startServer(app);

  try {
    // 1. Регистрация не позволяет назначить себе роль и отделы.
    const regRes = await request('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: 'alice_sec',
        password: 'alicePassword123',
        role: 'admin',
        departments: ['finance', 'it'],
      }),
    });
    assert.equal(regRes.status, 201);
    const regData = await regRes.json();
    assert.equal(regData.user.role, 'employee');
    assert.deepEqual(regData.user.departments, ['general']);
    const cookie = regRes.headers.get('set-cookie');

    // 2. Создаем диалог
    const createConvRes = await request('/api/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Cookie: cookie },
      body: JSON.stringify({ title: 'Безопасный диалог' }),
    });
    const { conversation } = await createConvRes.json();

    // 3. Отправляем сообщение
    const chatRes = await request('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Cookie: cookie },
      body: JSON.stringify({
        conversation_id: conversation.id,
        message: 'Покажи отчет по финансам',
      }),
    });
    assert.equal(chatRes.status, 200);

    // 4. Проверяем переданные заголовки и тело в APIAgent
    assert.ok(capturedHeaders['X-Internal-Token'], 'Должен передаваться заголовок X-Internal-Token');
    assert.equal(capturedBody.user_context.role, 'employee');
    assert.deepEqual(capturedBody.user_context.departments, ['general']);
  } finally {
    await close();
  }
});
