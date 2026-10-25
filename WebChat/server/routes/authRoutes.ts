import { Router, type Request, type Response } from 'express';
import type pg from 'pg';
import { getPool } from '../db.js';
import {
  hashPassword,
  verifyPassword,
  createSession,
  deleteSession,
  getSessionUser,
  SESSION_COOKIE_NAME,
  SESSION_MAX_AGE_MS,
  type AuthenticatedRequest,
} from '../auth.js';

export function createAuthRouter(poolProvider: () => pg.Pool = getPool): Router {
  const router = Router();

  const getCookieOptions = () => ({
    httpOnly: true,
    sameSite: 'strict' as const,
    maxAge: SESSION_MAX_AGE_MS,
    secure: process.env.NODE_ENV === 'production',
    path: '/',
  });

  // POST /api/auth/register
  router.post('/register', async (req: Request, res: Response): Promise<void> => {
    const { username, password } = req.body || {};

    if (!username || typeof username !== 'string' || username.trim().length < 3 || username.trim().length > 64) {
      res.status(400).json({ detail: 'Имя пользователя должно содержать от 3 до 64 символов.' });
      return;
    }

    if (!password || typeof password !== 'string' || password.length < 6) {
      res.status(400).json({ detail: 'Пароль должен содержать не менее 6 символов.' });
      return;
    }

    const cleanUsername = username.trim();
    // Access attributes are assigned by the server, never by public registration.
    const userRole = 'employee';
    const userDepts = ['general'];
    const pool = poolProvider();

    try {
      // Проверка на существующего пользователя
      const existing = await pool.query('SELECT id FROM users WHERE username = $1', [cleanUsername]);
      if (existing.rows.length > 0) {
        res.status(409).json({ detail: 'Пользователь с таким именем уже зарегистрирован.' });
        return;
      }

      const passwordHash = await hashPassword(password);
      const insertResult = await pool.query(
        `INSERT INTO users (username, password_hash, role, departments)
         VALUES ($1, $2, $3, $4)
         RETURNING id, username, role, departments`,
        [cleanUsername, passwordHash, userRole, userDepts]
      );

      const user = insertResult.rows[0];
      const sessionId = await createSession(
        pool,
        user.id,
        req.headers['user-agent'],
        req.ip || req.socket.remoteAddress
      );

      res.cookie(SESSION_COOKIE_NAME, sessionId, getCookieOptions());
      res.status(201).json({
        user: {
          id: user.id,
          username: user.username,
          role: user.role,
          departments: user.departments,
        },
      });
    } catch (err: any) {
      console.error('[Auth] Registration error:', err.message);
      res.status(500).json({ detail: 'Ошибка сервера при регистрации.' });
    }
  });

  // POST /api/auth/login
  router.post('/login', async (req: Request, res: Response): Promise<void> => {
    const { username, password } = req.body || {};

    if (!username || !password || typeof username !== 'string' || typeof password !== 'string') {
      res.status(400).json({ detail: 'Необходимо указать имя пользователя и пароль.' });
      return;
    }

    const cleanUsername = username.trim();
    const pool = poolProvider();

    try {
      const result = await pool.query(
        'SELECT id, username, password_hash, role, departments FROM users WHERE username = $1',
        [cleanUsername]
      );

      if (result.rows.length === 0) {
        res.status(401).json({ detail: 'Неверное имя пользователя или пароль.' });
        return;
      }

      const user = result.rows[0];
      const valid = await verifyPassword(password, user.password_hash);
      if (!valid) {
        res.status(401).json({ detail: 'Неверное имя пользователя или пароль.' });
        return;
      }

      const sessionId = await createSession(
        pool,
        user.id,
        req.headers['user-agent'],
        req.ip || req.socket.remoteAddress
      );

      res.cookie(SESSION_COOKIE_NAME, sessionId, getCookieOptions());
      res.json({
        user: {
          id: user.id,
          username: user.username,
          role: user.role || 'employee',
          departments: user.departments || ['general'],
        },
      });
    } catch (err: any) {
      console.error('[Auth] Login error:', err.message);
      res.status(500).json({ detail: 'Ошибка сервера при авторизации.' });
    }
  });

  // POST /api/auth/logout
  router.post('/logout', async (req: Request, res: Response): Promise<void> => {
    const sessionId = req.cookies?.[SESSION_COOKIE_NAME];
    const pool = poolProvider();

    if (sessionId) {
      try {
        await deleteSession(pool, sessionId);
      } catch (err: any) {
        console.warn('[Auth] Error deleting session on logout:', err.message);
      }
    }

    res.clearCookie(SESSION_COOKIE_NAME, {
      httpOnly: true,
      sameSite: 'strict',
      secure: process.env.NODE_ENV === 'production',
      path: '/',
    });

    res.json({ ok: true });
  });

  // GET /api/auth/me
  router.get('/me', async (req: Request, res: Response): Promise<void> => {
    const sessionId = req.cookies?.[SESSION_COOKIE_NAME];
    if (!sessionId) {
      res.status(401).json({ detail: 'Необходима авторизация.' });
      return;
    }

    const pool = poolProvider();
    try {
      const user = await getSessionUser(pool, sessionId);
      if (!user) {
        res.status(401).json({ detail: 'Недействительная или истекшая сессия.' });
        return;
      }

      res.json({ user });
    } catch (err: any) {
      console.error('[Auth] Me check error:', err.message);
      res.status(500).json({ detail: 'Ошибка проверки сессии.' });
    }
  });

  return router;
}

export const authRoutes = createAuthRouter(getPool);
