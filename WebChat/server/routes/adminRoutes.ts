import { Router, raw, type RequestHandler } from 'express';
import type pg from 'pg';
import { getPool, checkDbHealth } from '../db.js';
import { createAuthMiddleware, type AuthenticatedRequest } from '../auth.js';

const roles = new Set(['employee', 'manager', 'programmer', 'admin']);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const docId = /^doc-(?:[0-9a-f]{8}|[0-9a-f]{32})$/;
const userColumns = 'id, username, role, departments, created_at';

function stringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.length <= 30 && value.every(
    item => typeof item === 'string' && item.trim().length > 0 && item.length <= 64
  );
}

export function createAdminRouter(
  poolProvider: () => pg.Pool = getPool,
  fetchFn: typeof fetch = fetch,
  agentUrl: () => string = () => process.env.ASSISTENT_URL || process.env.AGENTAI_URL || 'http://127.0.0.1:8000',
  documentsUrl: () => string = () => process.env.DOCUMENTS_URL || agentUrl()
): Router {
  const router = Router();
  router.use(createAuthMiddleware(poolProvider));
  router.use(((req: AuthenticatedRequest, res, next) => {
    if (req.user?.role !== 'admin') {
      res.status(403).json({ detail: 'Этот раздел доступен только администратору.' });
      return;
    }
    next();
  }) as RequestHandler);

  router.get('/overview', async (_req, res) => {
    try {
      const pool = poolProvider();
      const [counts, database, agent, documents] = await Promise.all([
        pool.query(`SELECT
          (SELECT COUNT(*)::int FROM users) AS users,
          (SELECT COUNT(*)::int FROM conversations) AS conversations,
          (SELECT COUNT(*)::int FROM messages) AS messages,
          (SELECT COUNT(*)::int FROM sessions WHERE expires_at > CURRENT_TIMESTAMP) AS sessions`),
        checkDbHealth(pool),
        fetchFn(`${agentUrl().replace(/\/+$/, '')}/health`, { signal: AbortSignal.timeout(5000) })
          .then(async response => response.ok ? await response.json() as {
            status: string; model?: string; provider?: string; documents_count?: number;
          } : null)
          .catch(() => null),
        fetchFn(`${documentsUrl().replace(/\/+$/, '')}/health`, { signal: AbortSignal.timeout(5000) })
          .then(async response => response.ok ? await response.json() as { documents_count?: number } : null)
          .catch(() => null),
      ]);
      res.json({
        counts: counts.rows[0], database,
        agent: agent ? {
          healthy: agent.status === 'healthy', model: agent.model,
          provider: agent.provider, chunks: documents?.documents_count,
        } : { healthy: false },
        checked_at: new Date().toISOString(),
      });
    } catch {
      res.status(503).json({ detail: 'Не удалось загрузить состояние системы. Повторите запрос.' });
    }
  });

  router.get('/users', async (req, res) => {
    const search = typeof req.query.search === 'string' ? req.query.search.trim().slice(0, 64) : '';
    const page = Math.max(1, Math.min(10000, Number(req.query.page) || 1));
    if (!Number.isInteger(page)) {
      res.status(400).json({ detail: 'Некорректный номер страницы.' });
      return;
    }
    try {
      const pool = poolProvider();
      // Literal substring search: user input does not become a wildcard expression.
      const [users, total] = await Promise.all([
        pool.query(`SELECT ${userColumns} FROM users
          WHERE strpos(lower(username), lower($1)) > 0
          ORDER BY created_at DESC, id LIMIT 20 OFFSET $2`, [search, (page - 1) * 20]),
        pool.query(`SELECT COUNT(*)::int AS count FROM users
          WHERE strpos(lower(username), lower($1)) > 0`, [search]),
      ]);
      res.json({ users: users.rows, total: total.rows[0].count, page, page_size: 20 });
    } catch {
      res.status(503).json({ detail: 'Не удалось загрузить пользователей. Повторите запрос.' });
    }
  });

  router.patch('/users/:id', async (req: AuthenticatedRequest, res) => {
    const { role, departments } = req.body || {};
    if (!uuid.test(String(req.params.id)) || !roles.has(role) || !stringList(departments)) {
      res.status(400).json({ detail: 'Укажите корректную роль и список отделов (до 30 значений).' });
      return;
    }
    let client: pg.PoolClient | undefined;
    try {
      client = await poolProvider().connect();
      await client.query('BEGIN');
      await client.query("SET LOCAL statement_timeout = '5s'");
      // Serialize role changes so two administrators cannot demote each other concurrently.
      await client.query('SELECT pg_advisory_xact_lock(864210)');
      const actor = await client.query('SELECT role FROM users WHERE id = $1', [req.user!.id]);
      if (actor.rows[0]?.role !== 'admin') {
        await client.query('ROLLBACK');
        res.status(403).json({ detail: 'Права администратора изменились. Обновите страницу.' });
        return;
      }
      const target = await client.query('SELECT role FROM users WHERE id = $1', [req.params.id]);
      if (!target.rows.length) {
        await client.query('ROLLBACK');
        res.status(404).json({ detail: 'Пользователь не найден.' });
        return;
      }
      if (target.rows[0].role === 'admin' && role !== 'admin') {
        const admins = await client.query("SELECT COUNT(*)::int AS count FROM users WHERE role = 'admin'");
        if (admins.rows[0].count <= 1) {
          await client.query('ROLLBACK');
          res.status(409).json({ detail: 'В системе должен остаться хотя бы один администратор.' });
          return;
        }
      }
      const updated = await client.query(`UPDATE users SET role = $1, departments = $2
        WHERE id = $3 RETURNING ${userColumns}`, [role, [...new Set(departments.map(d => d.trim()))], req.params.id]);
      await client.query('COMMIT');
      res.json({ user: updated.rows[0] });
    } catch {
      if (client) await client.query('ROLLBACK').catch(() => {});
      res.status(503).json({ detail: 'Не удалось сохранить права. Повторите запрос.' });
    } finally {
      client?.release();
    }
  });

  router.post('/documents/extract-office', raw({ type: 'application/octet-stream', limit: '10mb' }), async (req, res) => {
    const filename = typeof req.query.filename === 'string' ? req.query.filename : '';
    if (!filename || filename.length > 255 || !/\.(docx|odt)$/i.test(filename) || !Buffer.isBuffer(req.body)) {
      res.status(400).json({ detail: 'Передайте DOCX или ODT до 10 МБ.' }); return;
    }
    try {
      const response = await fetchFn(`${documentsUrl().replace(/\/+$/, '')}/api/v1/documents/extract-office?filename=${encodeURIComponent(filename)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/octet-stream', 'X-Internal-Token': process.env.DOCUMENTS_ADMIN_KEY || '' },
        body: new Uint8Array(req.body), signal: AbortSignal.timeout(120000),
      });
      const payload = await response.json().catch(() => ({ detail: 'Не удалось обработать документ.' }));
      res.status(response.status).json(payload);
    } catch {
      res.status(503).json({ detail: 'Сервис обработки документов недоступен.' });
    }
  });

  router.post('/documents/extract-pdf', raw({ type: 'application/pdf', limit: '10mb' }), async (req, res) => {
    if (!Buffer.isBuffer(req.body) || !req.body.subarray(0, 5).equals(Buffer.from('%PDF-'))) {
      res.status(400).json({ detail: 'Выберите корректный PDF-файл.' });
      return;
    }
    try {
      const response = await fetchFn(`${documentsUrl().replace(/\/+$/, '')}/api/v1/documents/extract-pdf`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/pdf', 'X-Internal-Token': process.env.DOCUMENTS_ADMIN_KEY || '' },
        body: new Uint8Array(req.body),
        // Cover the API's configurable 300s conversion limit plus transport time.
        signal: AbortSignal.timeout(330000),
      });
      if (!response.ok) {
        const status = [400, 413, 415, 422, 429, 503, 504].includes(response.status) ? response.status : 502;
        const data = await response.json().catch(() => ({})) as { detail?: unknown };
        res.status(status).json({ detail: typeof data.detail === 'string' ? data.detail : 'Не удалось извлечь текст PDF.' });
        return;
      }
      const data = await response.json() as { content?: unknown; pages?: unknown; engine?: unknown };
      if (typeof data.content !== 'string' || !data.content.trim() || data.content.length > 500000) {
        res.status(422).json({ detail: 'Не удалось получить текст PDF допустимого размера.' });
        return;
      }
      res.json({ content: data.content, pages: data.pages, engine: data.engine });
    } catch {
      res.status(502).json({ detail: 'Сервис обработки PDF недоступен. Повторите загрузку позже.' });
    }
  });

  router.post('/documents', async (req: AuthenticatedRequest, res) => {
    const { title, content, is_public = false, allowed_roles = ['employee'], allowed_departments = ['general'] } = req.body || {};
    if (typeof title !== 'string' || !title.trim() || title.length > 255 ||
        typeof content !== 'string' || !content.trim() || content.length > 500000 ||
        typeof is_public !== 'boolean' || !stringList(allowed_roles) || !stringList(allowed_departments)) {
      res.status(400).json({ detail: 'Проверьте название, текст (до 500 000 символов) и права доступа.' });
      return;
    }
    try {
      const response = await fetchFn(`${documentsUrl().replace(/\/+$/, '')}/api/v1/documents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Internal-Token': process.env.DOCUMENTS_ADMIN_KEY || '' },
        signal: AbortSignal.timeout(120000),
        body: JSON.stringify({ title: title.trim(), content: content.trim(), is_public,
          allowed_roles, allowed_departments, owner_id: req.user!.id }),
      });
      if (!response.ok) {
        res.status(502).json({ detail: 'Не удалось добавить документ. Проверьте состояние агента и повторите запрос.' });
        return;
      }
      const data = await response.json() as { document_id: string; title: string; chunks_count: number; status: string };
      res.status(201).json({ document_id: data.document_id, title: data.title, chunks_count: data.chunks_count, status: data.status });
    } catch {
      res.status(502).json({ detail: 'Сервис базы знаний недоступен или не успел ответить. Перед повторной отправкой проверьте состояние сервиса.' });
    }
  });

  router.get('/documents/catalog', async (req, res) => {
    const page = Math.max(1, Math.min(10000, Number(req.query.page) || 1));
    const pageSize = Math.max(1, Math.min(100, Number(req.query.page_size) || 20));
    const status = typeof req.query.status === 'string' ? req.query.status : '';
    const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (status) query.set('status', status);

    try {
      const response = await fetchFn(`${documentsUrl().replace(/\/+$/, '')}/api/v1/documents/catalog?${query.toString()}`, {
        headers: { 'X-Internal-Token': process.env.DOCUMENTS_ADMIN_KEY || '' },
        signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) {
        res.status(response.status >= 500 ? 502 : response.status).json({ detail: 'Не удалось загрузить каталог документов.' });
        return;
      }
      const data = await response.json();
      res.json(data);
    } catch {
      res.status(502).json({ detail: 'Сервис базы знаний недоступен.' });
    }
  });

  router.get('/documents/download/:id', async (req: AuthenticatedRequest, res) => {
    const docIdParam = String(req.params.id);
    if (!docId.test(docIdParam)) {
      res.status(400).json({ detail: 'Некорректный идентификатор документа.' });
      return;
    }
    try {
      const userRole = req.user?.role || 'employee';
      const downloadContext = new URLSearchParams({ user_role: userRole, user_id: String(req.user?.id || ''), departments: (req.user?.departments || []).join(',') });
      const response = await fetchFn(`${documentsUrl().replace(/\/+$/, '')}/api/v1/documents/download/${encodeURIComponent(docIdParam)}?${downloadContext}`, {
        headers: { 'X-Internal-Token': process.env.DOCUMENTS_ADMIN_KEY || '' },
        signal: AbortSignal.timeout(60000),
      });
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({})) as { detail?: string };
        res.status(response.status).json({ detail: errorData.detail || 'Не удалось скачать файл.' });
        return;
      }
      const contentType = response.headers.get('content-type') || 'application/octet-stream';
      const disposition = response.headers.get('content-disposition');
      res.setHeader('Content-Type', contentType);
      if (disposition) res.setHeader('Content-Disposition', disposition);

      const arrayBuffer = await response.arrayBuffer();
      res.send(Buffer.from(arrayBuffer));
    } catch {
      res.status(502).json({ detail: 'Сервис базы знаний недоступен.' });
    }
  });

  router.post('/documents/bulk', raw({ type: 'multipart/form-data', limit: '50mb' }), async (req: AuthenticatedRequest, res) => {
    const contentType = req.headers['content-type'];
    if (!contentType || !contentType.startsWith('multipart/form-data')) {
      res.status(400).json({ detail: 'Ожидается multipart/form-data.' });
      return;
    }
    const isPublic = req.query.is_public === 'true';
    const allowedRoles = typeof req.query.allowed_roles === 'string' ? req.query.allowed_roles : 'employee';
    const allowedDepts = typeof req.query.allowed_departments === 'string' ? req.query.allowed_departments : 'general';

    const query = new URLSearchParams({
      is_public: String(isPublic),
      allowed_roles: allowedRoles,
      allowed_departments: allowedDepts,
      owner_id: req.user!.id,
    });
    if (req.query.version_parent_id !== undefined) {
      if (typeof req.query.version_parent_id !== 'string' || !docId.test(req.query.version_parent_id)) {
        res.status(400).json({ detail: 'Некорректный документ для новой версии.' });
        return;
      }
      query.set('version_parent_id', req.query.version_parent_id);
    }

    try {
      const response = await fetchFn(`${documentsUrl().replace(/\/+$/, '')}/api/v1/documents/bulk-upload?${query.toString()}`, {
        method: 'POST',
        headers: {
          'Content-Type': contentType,
          'X-Internal-Token': process.env.DOCUMENTS_ADMIN_KEY || '',
        },
        body: new Uint8Array(req.body),
        signal: AbortSignal.timeout(60000),
      });
      if (!response.ok) {
        const errData = await response.json().catch(() => ({})) as { detail?: string };
        res.status(response.status >= 500 ? 502 : response.status).json({ detail: errData.detail || 'Не удалось загрузить пакет файлов.' });
        return;
      }
      const data = await response.json();
      res.status(202).json(data);
    } catch {
      res.status(502).json({ detail: 'Сервис базы знаний недоступен.' });
    }
  });

  router.get('/documents/bulk-status', async (req, res) => {
    const taskIds = typeof req.query.task_ids === 'string' ? req.query.task_ids : '';
    try {
      const response = await fetchFn(`${documentsUrl().replace(/\/+$/, '')}/api/v1/documents/bulk-status?task_ids=${encodeURIComponent(taskIds)}`, {
        headers: { 'X-Internal-Token': process.env.DOCUMENTS_ADMIN_KEY || '' },
        signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) {
        res.status(response.status >= 500 ? 502 : response.status).json({ detail: 'Не удалось получить статусы.' });
        return;
      }
      const data = await response.json();
      res.json(data);
    } catch {
      res.status(502).json({ detail: 'Сервис базы знаний недоступен.' });
    }
  });
  router.post('/documents/bulk-retry/:id', async (req, res) => {
    const taskId = String(req.params.id);
    if (!/^[0-9a-f]{32}$/.test(taskId)) {
      res.status(400).json({ detail: 'Некорректное задание.' });
      return;
    }
    try {
      const response = await fetchFn(`${documentsUrl().replace(/\/+$/, '')}/api/v1/documents/bulk-retry/${taskId}`, {
        method: 'POST',
        headers: { 'X-Internal-Token': process.env.DOCUMENTS_ADMIN_KEY || '' },
        signal: AbortSignal.timeout(10000),
      });
      const data = await response.json();
      res.status(response.status >= 500 ? 502 : response.status).json(data);
    } catch { res.status(502).json({ detail: 'Сервис базы знаний недоступен.' }); }
  });
  router.get('/documents/versions/:id', async (req, res) => {
    const identifier = String(req.params.id);
    if (!docId.test(identifier)) { res.status(400).json({ detail: 'Некорректный документ.' }); return; }
    try {
      const response = await fetchFn(`${documentsUrl().replace(/\/+$/, '')}/api/v1/documents/versions/${identifier}`, {
        headers: { 'X-Internal-Token': process.env.DOCUMENTS_ADMIN_KEY || '' },
        signal: AbortSignal.timeout(10000),
      });
      res.status(response.status >= 500 ? 502 : response.status).json(await response.json());
    } catch { res.status(502).json({ detail: 'Не удалось получить историю версий.' }); }
  });

  const uploadAction = (action: 'review' | 'cancel', method: 'GET' | 'POST'): RequestHandler => async (req: AuthenticatedRequest, res) => {
    const taskId = String(req.params.id);
    if (!/^[0-9a-f]{32}$/.test(taskId)) {
      res.status(400).json({ detail: 'Некорректное задание.' });
      return;
    }
    let body: string | undefined;
    if (action === 'review' && method === 'POST') {
      const { content, review_token } = req.body || {};
      if (typeof content !== 'string' || !content.trim() || content.length > 500000 ||
          typeof review_token !== 'string' || !/^[0-9a-f]{32}$/.test(review_token)) {
        res.status(400).json({ detail: 'Проверьте текст и повторно откройте задание.' });
        return;
      }
      body = JSON.stringify({ content, review_token });
    }
    try {
      const query = action === 'review' && method === 'POST'
        ? `?reviewer_id=${encodeURIComponent(String(req.user!.id))}` : '';
      const response = await fetchFn(`${documentsUrl().replace(/\/+$/, '')}/api/v1/documents/bulk-${action}/${taskId}${query}`, {
        method, body,
        headers: { 'X-Internal-Token': process.env.DOCUMENTS_ADMIN_KEY || '', 'Content-Type': 'application/json' },
        signal: AbortSignal.timeout(10000),
      });
      res.status(response.status >= 500 ? 502 : response.status).json(await response.json());
    } catch { res.status(502).json({ detail: 'Сервис базы знаний недоступен.' }); }
  };
  router.get('/documents/bulk-review/:id', uploadAction('review', 'GET'));
  router.post('/documents/bulk-review/:id', uploadAction('review', 'POST'));
  router.post('/documents/bulk-cancel/:id', uploadAction('cancel', 'POST'));
  const publication: RequestHandler = async (req: AuthenticatedRequest, res) => {
    const identifier = String(req.params.id);
    if (!docId.test(identifier)) { res.status(400).json({ detail: 'Некорректный документ.' }); return; }
    let body: string | undefined;
    if (req.method === 'POST') {
      const { title, content } = req.body || {};
      if (typeof title !== 'string' || !title.trim() || title.length > 255 || typeof content !== 'string' || !content.trim() || content.length > 50000) {
        res.status(400).json({ detail: 'Укажите заголовок и проверенный текст до 50 000 символов.' }); return;
      }
      body = JSON.stringify({ title, content });
    }
    try {
      const query = req.method === 'POST' ? `?publisher_id=${encodeURIComponent(String(req.user!.id))}` : '';
      const response = await fetchFn(`${documentsUrl().replace(/\/+$/, '')}/api/v1/documents/publication/${identifier}${query}`, {
        method: req.method, body,
        headers: { 'X-Internal-Token': process.env.DOCUMENTS_ADMIN_KEY || '', 'Content-Type': 'application/json' },
        signal: AbortSignal.timeout(10000),
      });
      res.status(response.status >= 500 ? 502 : response.status).json(await response.json());
    } catch { res.status(502).json({ detail: 'Сервис публикации недоступен.' }); }
  };
  router.get('/documents/publication/:id', publication);
  router.post('/documents/publication/:id', publication);
  router.delete('/documents/publication/:id', publication);
  return router;
}
