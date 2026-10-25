import express, { type Request, type Response, type NextFunction } from 'express';
import cookieParser from 'cookie-parser';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';
import { bootstrapAdmin, readBootstrapAdminConfig } from './bootstrapAdmin.js';
import { initDb, checkDbHealth, getPool, type setPool } from './db.js';
import { createAuthMiddleware } from './auth.js';
import { authRoutes, createAuthRouter } from './routes/authRoutes.js';
import { conversationRoutes, createConversationRouter } from './routes/conversationRoutes.js';
import { chatRoutes, createChatRouter } from './routes/chatRoutes.js';
import { createAdminRouter } from './routes/adminRoutes.js';
import type pg from 'pg';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export interface AppOptions {
  pool?: pg.Pool;
  agentUrl?: string;
  fetchFn?: typeof fetch;
}

export function createApp(options?: AppOptions): express.Express {
  const app = express();

  // 1. Базовые middleware
  app.use(express.json({ limit: '28mb' }));
  app.use(express.urlencoded({ extended: true, limit: '28mb' }));
  app.use(cookieParser());

  // 2. Заголовки безопасности
  app.use((req: Request, res: Response, next: NextFunction) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    if (req.path.startsWith('/api/') || req.path === '/health') {
      res.setHeader('Cache-Control', 'no-store');
    }
    next();
  });

  // 3. Healthcheck эндпоинт
  app.get('/health', async (_req: Request, res: Response) => {
    const isDbHealthy = await checkDbHealth(options?.pool);
    res.json({
      status: 'ok',
      db: isDbHealthy,
    });
  });

  // 4. API маршруты
  app.use('/api/admin', createAdminRouter(
    () => options?.pool || getPool(), options?.fetchFn,
    () => options?.agentUrl || process.env.AGENTAI_URL || 'http://127.0.0.1:8000'
  ));
  if (options?.pool) {
    const poolProvider = () => options.pool!;
    const authMw = createAuthMiddleware(poolProvider);
    app.use('/api/auth', createAuthRouter(poolProvider));
    app.use('/api/conversations', createConversationRouter(poolProvider, authMw));
    app.use('/api/chat', createChatRouter(
      poolProvider,
      authMw,
      options.fetchFn,
      () => options.agentUrl || process.env.AGENTAI_URL || 'http://127.0.0.1:8000'
    ));
  } else {
    app.use('/api/auth', authRoutes);
    app.use('/api/conversations', conversationRoutes);
    app.use('/api/chat', chatRoutes);
  }

  // 5. Статические файлы (dist от Vite или fallback на public)
  const distPath = path.resolve(__dirname, '../dist');
  const publicPath = path.resolve(__dirname, '../public');

  if (fs.existsSync(distPath)) {
    app.use(express.static(distPath));
    // SPA fallback для не-API запросов
    app.get('*', (req: Request, res: Response, next: NextFunction) => {
      if (req.path.startsWith('/api/') || req.path === '/health') {
        return next();
      }
      res.sendFile(path.join(distPath, 'index.html'));
    });
  } else if (fs.existsSync(publicPath)) {
    app.use(express.static(publicPath));
  }

  // 6. Обработчик неизвестных API маршрутов
  app.use('/api/*', (_req: Request, res: Response) => {
    res.status(404).json({ detail: 'API эндпоинт не найден' });
  });

  // 7. Глобальный обработчик ошибок
  app.use((err: any, _req: Request, res: Response, _next: NextFunction) => {
    console.error('[Server] Unhandled error:', err);
    res.status(500).json({ detail: 'Внутренняя ошибка сервера' });
  });

  return app;
}

export const app = createApp();

export async function startServer(port = parseInt(process.env.PORT || '3000', 10), host = process.env.HOST || '0.0.0.0') {
  const firstAdmin = readBootstrapAdminConfig();
  // Пытаемся инициализировать БД, но не блокируем запуск сервера при ее временной недоступности
  const dbReady = await initDb();
  if (firstAdmin && !dbReady) {
    throw new Error('Невозможно создать первого администратора: база пользователей недоступна.');
  }
  const bootstrapResult = await bootstrapAdmin(getPool(), firstAdmin);
  if (bootstrapResult === 'created') console.log('[Auth] First administrator created.');

  return new Promise<import('node:http').Server>((resolve) => {
    const server = app.listen(port, host, () => {
      console.log(`[Server] WebChat Express server listening on http://${host}:${port}`);
      resolve(server);
    });
  });
}

// Запуск при прямом вызове
const isMain = process.argv[1] && (
  path.resolve(process.argv[1]) === path.resolve(__filename) ||
  process.argv[1].endsWith('server/index.ts') ||
  process.argv[1].endsWith('server\\index.ts')
);

if (isMain && process.env.NODE_ENV !== 'test') {
  startServer().catch((err) => {
    console.error('[Server] Fatal startup error:', err);
    process.exit(1);
  });
}
