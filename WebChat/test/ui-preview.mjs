// Изолированное визуальное QA: все ответы API являются фикстурами. Никогда не монтируется сервером приложения.
import express from 'express';
import { fileURLToPath } from 'node:url';
const app = express();
app.use(express.json());
const users = [
  { id: '00000000-0000-0000-0000-000000000001', username: 'admin', role: 'admin', departments: ['general'], created_at: '2026-10-25T08:00:00Z' },
  { id: '00000000-0000-0000-0000-000000000002', username: 'demo.employee', role: 'employee', departments: ['general'], created_at: '2026-10-24T08:00:00Z' },
  { id: '00000000-0000-0000-0000-000000000003', username: 'demo.manager', role: 'manager', departments: ['finance', 'it'], created_at: '2026-10-23T08:00:00Z' },
];
app.get('/api/auth/me', (_req, res) => res.json({ user: users[0] }));
app.get('/api/conversations', (_req, res) => res.json({ conversations: [] }));
app.get('/api/admin/overview', (_req, res) => res.json({ counts: { users: 3, conversations: 8, messages: 42, sessions: 2 }, database: true,
  agent: { healthy: true, model: 'Локальная модель (тест)', provider: 'lmstudio', chunks: 24 }, checked_at: new Date().toISOString() }));
app.get('/api/admin/users', (req, res) => {
  const filtered = users.filter(user => user.username.includes(String(req.query.search || '')));
  res.json({ users: filtered, total: filtered.length, page: 1, page_size: 20 });
});
app.patch('/api/admin/users/:id', (req, res) => {
  const user = users.find(user => user.id === req.params.id);
  if (!user) return res.sendStatus(404);
  Object.assign(user, req.body); res.json({ user });
});
app.post('/api/admin/documents', (req, res) => res.status(201).json({ document_id: 'preview-only', title: req.body.title, chunks_count: 2 }));
app.post('/api/admin/documents/extract-pdf', express.raw({ type: 'application/pdf', limit: '10mb' }), (_req, res) => {
  setTimeout(() => res.json({ content: 'Согласование: 3 рабочих дня.\nПроверьте текст перед добавлением.', pages: 1, engine: 'docling-cpu' }), 500);
});
app.post('/api/auth/logout', (_req, res) => res.json({ ok: true }));
app.use(express.static(fileURLToPath(new URL('../dist', import.meta.url))));
app.listen(3100, '0.0.0.0', () => console.log('UI fixture preview on port 3100. No production API or database access.'));
