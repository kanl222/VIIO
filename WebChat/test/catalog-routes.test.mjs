// Explicit test-only credentials; production has no built-in service token.
process.env.CHAT_API_KEY = 'c'.repeat(40);
process.env.DOCUMENTS_READ_KEY = 'r'.repeat(40);
process.env.DOCUMENTS_ADMIN_KEY = 'a'.repeat(40);
import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { createApp } from '../dist-server/index.js';

class MockPool {
  constructor() {
    this.users = new Map();
    this.sessions = new Map();
  }

  async connect() {
    return {
      query: (sql, params) => this.query(sql, params),
      release: () => {},
    };
  }

  async query(sql, params = []) {
    const s = sql.replace(/\s+/g, ' ').trim();
    if (s.includes('FROM sessions s') && s.includes('JOIN users u')) {
      const sessionId = params[0];
      const session = this.sessions.get(sessionId);
      if (!session) return { rows: [] };
      const user = this.users.get(session.user_id);
      if (!user) return { rows: [] };
      return {
        rows: [{
          id: user.id,
          username: user.username,
          role: user.role,
          departments: user.departments,
          session_id: session.id,
          expires_at: session.expires_at,
        }],
      };
    }
    if (s.startsWith('SELECT 1')) return { rowCount: 1, rows: [{ '?column?': 1 }] };
    return { rows: [] };
  }
}

async function startServer(app) {
  const pipe = `\\\\.\\pipe\\webchat-test-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const server = app.listen(pipe);
  await once(server, 'listening');

  const request = (path, options = {}) => {
    return new Promise((resolve, reject) => {
      const headers = { ...(options.headers || {}) };
      let bodyData = null;

      if (options.body) {
        bodyData = Buffer.isBuffer(options.body) || typeof options.body === 'string' ? options.body : JSON.stringify(options.body);
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
              buffer: async () => buffer,
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
    close: () => new Promise(resolve => server.close(resolve)),
  };
}

async function setupTestApp(fetchFn) {
  const pool = new MockPool();
  const adminId = '11111111-1111-1111-1111-111111111111';
  const employeeId = '22222222-2222-2222-2222-222222222222';

  pool.users.set(adminId, {
    id: adminId, username: 'admin', role: 'admin', departments: ['it'],
  });
  pool.users.set(employeeId, {
    id: employeeId, username: 'emp', role: 'employee', departments: ['general'],
  });

  pool.sessions.set('admin-sess', {
    id: 'admin-sess', user_id: adminId, expires_at: new Date(Date.now() + 3600000),
  });
  pool.sessions.set('emp-sess', {
    id: 'emp-sess', user_id: employeeId, expires_at: new Date(Date.now() + 3600000),
  });

  const app = createApp({
    pool,
    agentUrl: 'http://agentai:8000',
    fetchFn,
  });

  return startServer(app);
}

test('Admin /documents/catalog forwards pagination and returns items', async () => {
  let calledUrl = '';
  const mockFetch = async (url) => {
    calledUrl = String(url);
    return {
      ok: true,
      status: 200,
      json: async () => ({
        items: [{ id: 'doc-12345678', title: 'Test Document', version: 1, chunks_count: 3 }],
        total: 1,
        page: 1,
        page_size: 20,
      }),
    };
  };

  const { request, close } = await setupTestApp(mockFetch);
  try {
    const res = await request('/api/admin/documents/catalog?page=1&page_size=20', {
      headers: { cookie: 'session_id=admin-sess' },
    });
    assert.equal(res.status, 200);
    const data = await res.json();
    assert.equal(data.total, 1);
    assert.equal(data.items[0].id, 'doc-12345678');
    assert.match(calledUrl, /\/api\/v1\/documents\/catalog\?page=1&page_size=20/);
  } finally {
    await close();
  }
});

test('Admin /documents/download/:id returns original file with headers', async () => {
  let calledUrl = '';
  const fileBytes = Buffer.from([1, 2, 3, 4]);
  const mockFetch = async (url) => {
    calledUrl = String(url);
    return {
      ok: true,
      status: 200,
      headers: {
        get(name) {
          if (name.toLowerCase() === 'content-type') return 'application/pdf';
          if (name.toLowerCase() === 'content-disposition') return 'attachment; filename="doc.pdf"';
          return null;
        },
      },
      arrayBuffer: async () => fileBytes.buffer.slice(fileBytes.byteOffset, fileBytes.byteOffset + fileBytes.byteLength),
    };
  };

  const { request, close } = await setupTestApp(mockFetch);
  try {
    const res = await request('/api/admin/documents/download/doc-12345678', {
      headers: { cookie: 'session_id=admin-sess' },
    });
    assert.equal(res.status, 200);
    assert.equal(res.headers.get('content-type'), 'application/pdf');
    assert.match(res.headers.get('content-disposition'), /attachment/);
    const buf = await res.buffer();
    assert.equal(buf.length, 4);
    assert.match(calledUrl, /\/api\/v1\/documents\/download\/doc-12345678\?user_role=admin/);
  } finally {
    await close();
  }
});

test('Chat /api/chat/download/:id allows permitted employee to download file', async () => {
  let calledUrl = '';
  const textContent = 'Text file content';
  const mockFetch = async (url) => {
    calledUrl = String(url);
    return {
      ok: true,
      status: 200,
      headers: {
        get(name) {
          if (name.toLowerCase() === 'content-type') return 'text/plain';
          if (name.toLowerCase() === 'content-disposition') return 'attachment; filename="guide.txt"';
          return null;
        },
      },
      arrayBuffer: async () => new TextEncoder().encode(textContent).buffer,
    };
  };

  const { request, close } = await setupTestApp(mockFetch);
  try {
    const res = await request('/api/chat/download/doc-12345678', {
      headers: { cookie: 'session_id=emp-sess' },
    });
    assert.equal(res.status, 200);
    assert.equal(res.headers.get('content-type'), 'text/plain');
    const text = await res.text();
    assert.equal(text, textContent);
    assert.match(calledUrl, /\/api\/v1\/documents\/download\/doc-12345678\?user_role=employee/);
  } finally {
    await close();
  }
});

test('Admin /documents/bulk forwards multipart payload and receives tasks', async () => {
  let calledUrl = '';
  let receivedMethod = '';
  const mockFetch = async (url, opts) => {
    calledUrl = String(url);
    receivedMethod = opts.method;
    return {
      ok: true,
      status: 202,
      json: async () => ({
        tasks: [{ task_id: 'task-100', filename: 'sample.pdf', status: 'queued' }],
      }),
    };
  };

  const { request, close } = await setupTestApp(mockFetch);
  try {
    const boundary = '----WebKitFormBoundaryTest123456';
    const body = `--${boundary}\r\nContent-Disposition: form-data; name="files"; filename="test.txt"\r\nContent-Type: text/plain\r\n\r\ntest content\r\n--${boundary}--\r\n`;

    const res = await request('/api/admin/documents/bulk?is_public=false&allowed_roles=employee', {
      method: 'POST',
      headers: {
        cookie: 'session_id=admin-sess',
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
      },
      body,
    });

    assert.equal(res.status, 202);
    const data = await res.json();
    assert.equal(data.tasks.length, 1);
    assert.equal(data.tasks[0].task_id, 'task-100');
    assert.equal(receivedMethod, 'POST');
    assert.match(calledUrl, /\/api\/v1\/documents\/bulk-upload/);
  } finally {
    await close();
  }
});


test('Download uses session access, ignoring forged query context and accepting full IDs', async () => {
  let calledUrl = '';
  const { request, close } = await setupTestApp(async (url) => {
    calledUrl = String(url);
    return { ok: true, status: 200, headers: new Headers(), arrayBuffer: async () => new ArrayBuffer(0) };
  });
  try {
    const response = await request('/api/chat/download/doc-0123456789abcdef0123456789abcdef?user_role=admin&user_id=forged&departments=hr', { headers: { cookie: 'session_id=emp-sess' } });
    assert.equal(response.status, 200);
    const context = new URL(calledUrl).searchParams;
    assert.equal(context.get('user_role'), 'employee');
    assert.equal(context.get('user_id'), '22222222-2222-2222-2222-222222222222');
    assert.equal(context.get('departments'), 'general');
  } finally { await close(); }
});

test('Retry upload is restricted to admin and forwards POST to the agent', async () => {
  let calls = 0;
  const task = '0123456789abcdef0123456789abcdef';
  const { request, close } = await setupTestApp(async (url, options) => {
    calls++;
    assert.equal(options.method, 'POST');
    assert.ok(String(url).endsWith('/api/v1/documents/bulk-retry/' + task));
    return { ok: true, status: 200, json: async () => ({ status: 'queued', task_id: task }) };
  });
  try {
    assert.equal((await request('/api/admin/documents/bulk-retry/' + task, { method: 'POST', headers: { cookie: 'session_id=emp-sess' } })).status, 403);
    assert.equal(calls, 0);
    assert.equal((await request('/api/admin/documents/bulk-retry/' + task, { method: 'POST', headers: { cookie: 'session_id=admin-sess' } })).status, 200);
    assert.equal(calls, 1);
  } finally { await close(); }
});


test('Review and cancellation reject employees and anonymous callers before forwarding', async () => {
  let calls = 0;
  const { request, close } = await setupTestApp(async () => { calls++; throw new Error('must not forward'); });
  try {
    const task = '0123456789abcdef0123456789abcdef';
    for (const [method, action] of [['GET', 'bulk-review'], ['POST', 'bulk-review'], ['POST', 'bulk-cancel']]) {
      for (const [cookie, status] of [['session_id=emp-sess', 403], ['', 401]]) {
        const response = await request(`/api/admin/documents/${action}/${task}`, { method, headers: { cookie } });
        assert.equal(response.status, status);
      }
    }
    assert.equal(calls, 0);
  } finally { await close(); }
});

test('Review approval uses session reviewer and forwards only validated content and token', async () => {
  let calls = 0;
  const token = '0123456789abcdef0123456789abcdef';
  const { request, close } = await setupTestApp(async (url, options) => {
    calls++;
    assert.equal(new URL(String(url)).searchParams.get('reviewer_id'), '11111111-1111-1111-1111-111111111111');
    assert.deepEqual(JSON.parse(options.body), { content: 'Approved text', review_token: token });
    return { status: 200, json: async () => ({ status: 'queued' }) };
  });
  try {
    const response = await request(`/api/admin/documents/bulk-review/${token}?reviewer_id=forged`, {
      method: 'POST', headers: { cookie: 'session_id=admin-sess', 'Content-Type': 'application/json' },
      body: { content: 'Approved text', review_token: token, reviewer_id: 'forged', is_public: true },
    });
    assert.equal(response.status, 200);
    assert.equal(calls, 1);
    const invalid = await request(`/api/admin/documents/bulk-review/${token}`, {
      method: 'POST', headers: { cookie: 'session_id=admin-sess', 'Content-Type': 'application/json' },
      body: { content: ' ', review_token: token },
    });
    assert.equal(invalid.status, 400);
    assert.equal(calls, 1);
  } finally { await close(); }
});

test('Review and cancel preserve conflict responses from agent', async () => {
  const { request, close } = await setupTestApp(async () => ({ status: 409, json: async () => ({ detail: 'Already processed' }) }));
  try {
    const task = '0123456789abcdef0123456789abcdef';
    assert.equal((await request(`/api/admin/documents/bulk-review/${task}`, { headers: { cookie: 'session_id=admin-sess' } })).status, 409);
    assert.equal((await request(`/api/admin/documents/bulk-cancel/${task}`, { method: 'POST', headers: { cookie: 'session_id=admin-sess' } })).status, 409);
  } finally { await close(); }
});


test('Version history is admin-only and forwards the document identity', async () => {
  let calls = 0;
  const doc = 'doc-0123456789abcdef0123456789abcdef';
  const { request, close } = await setupTestApp(async url => {
    calls++;
    assert.ok(String(url).endsWith('/api/v1/documents/versions/' + doc));
    return { status: 200, json: async () => ({ items: [{ id: doc, version: 2, is_active: true }] }) };
  });
  try {
    assert.equal((await request('/api/admin/documents/versions/' + doc, { headers: { cookie: 'session_id=emp-sess' } })).status, 403);
    assert.equal(calls, 0);
    const response = await request('/api/admin/documents/versions/' + doc, { headers: { cookie: 'session_id=admin-sess' } });
    assert.equal(response.status, 200);
    assert.equal((await response.json()).items[0].is_active, true);
  } finally { await close(); }
});

test('New-version upload preserves target ID and trusted session owner', async () => {
  const doc = 'doc-0123456789abcdef0123456789abcdef';
  const { request, close } = await setupTestApp(async (url, options) => {
    const query = new URL(String(url)).searchParams;
    assert.equal(query.get('version_parent_id'), doc);
    assert.equal(query.get('owner_id'), '11111111-1111-1111-1111-111111111111');
    assert.equal(options.method, 'POST');
    return { ok: true, status: 202, json: async () => ({ tasks: [] }) };
  });
  try {
    const response = await request(`/api/admin/documents/bulk?version_parent_id=${doc}&owner_id=forged`, {
      method: 'POST', headers: { cookie: 'session_id=admin-sess', 'Content-Type': 'multipart/form-data; boundary=x' }, body: '--x--',
    });
    assert.equal(response.status, 202);
  } finally { await close(); }
});


test('External publication is restricted to admin and uses session publisher', async () => {
  let calls = 0;
  const doc = 'doc-0123456789abcdef0123456789abcdef';
  const { request, close } = await setupTestApp(async (url, options) => {
    calls++;
    if (options.method === 'POST') {
      assert.equal(new URL(String(url)).searchParams.get('publisher_id'), '11111111-1111-1111-1111-111111111111');
      assert.deepEqual(JSON.parse(options.body), { title: 'Public help', content: 'Approved public text' });
    }
    return { status: 200, json: async () => ({ status: 'published' }) };
  });
  try {
    for (const method of ['GET', 'POST', 'DELETE']) {
      assert.equal((await request(`/api/admin/documents/publication/${doc}`, { method, headers: { cookie: 'session_id=emp-sess' } })).status, 403);
    }
    assert.equal(calls, 0);
    assert.equal((await request(`/api/admin/documents/publication/${doc}?publisher_id=forged`, {
      method: 'POST', headers: { cookie: 'session_id=admin-sess', 'Content-Type': 'application/json' },
      body: { title: 'Public help', content: 'Approved public text', publisher_id: 'forged', user_context: { role: 'admin' } },
    })).status, 200);
    assert.equal(calls, 1);
  } finally { await close(); }
});

test('Publication rejects empty public excerpts without forwarding', async () => {
  let calls = 0;
  const { request, close } = await setupTestApp(async () => { calls++; throw Error('must not forward'); });
  try {
    const response = await request('/api/admin/documents/publication/doc-12345678', {
      method: 'POST', headers: { cookie: 'session_id=admin-sess', 'Content-Type': 'application/json' },
      body: { title: 'Title', content: ' ' },
    });
    assert.equal(response.status, 400);
    assert.equal(calls, 0);
  } finally { await close(); }
});
