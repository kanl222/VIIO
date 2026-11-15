import bcrypt from 'bcryptjs';
import { v4 as uuidv4 } from 'uuid';
import type { Request, Response, NextFunction } from 'express';
import type pg from 'pg';
import { getPool } from './db.js';

export const SESSION_COOKIE_NAME = 'session_id';
export const SESSION_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000; // 30 days

export interface AuthenticatedUser {
  id: string;
  username: string;
  role: string;
  departments: string[];
}

export interface AuthenticatedRequest extends Request {
  user?: AuthenticatedUser;
}

/**
 * Хэширование пароля пользователя с помощью bcrypt.
 */
export async function hashPassword(password: string): Promise<string> {
  return bcrypt.hash(password, 10);
}

/**
 * Проверка пароля пользователя относительно хэша bcrypt.
 */
export async function verifyPassword(password: string, hash: string): Promise<boolean> {
  return bcrypt.compare(password, hash);
}

/**
 * Создание новой 30-дневной сессии в PostgreSQL user_db.
 */
export async function createSession(
  pool: pg.Pool,
  userId: string,
  userAgent?: string,
  ip?: string
): Promise<string> {
  const sessionId = uuidv4();
  const expiresAt = new Date(Date.now() + SESSION_MAX_AGE_MS);

  await pool.query(
    `INSERT INTO sessions (id, user_id, expires_at, user_agent, ip_address)
     VALUES ($1, $2, $3, $4, $5)`,
    [sessionId, userId, expiresAt, userAgent || null, ip || null]
  );

  return sessionId;
}

/**
 * Удаление сессии из PostgreSQL user_db.
 */
export async function deleteSession(pool: pg.Pool, sessionId: string): Promise<void> {
  if (!sessionId) return;
  await pool.query('DELETE FROM sessions WHERE id = $1', [sessionId]);
}

/**
 * Получение данных пользователя по активной сессии.
 */
export async function getSessionUser(
  pool: pg.Pool,
  sessionId: string
): Promise<AuthenticatedUser | null> {
  if (!sessionId) return null;

  const result = await pool.query(
    `SELECT u.id, u.username, u.role, u.departments
     FROM sessions s
     JOIN users u ON s.user_id = u.id
     WHERE s.id = $1 AND s.expires_at > CURRENT_TIMESTAMP`,
    [sessionId]
  );

  if (result.rows.length === 0) {
    return null;
  }

  const row = result.rows[0];
  return {
    id: row.id,
    username: row.username,
    role: row.role || 'employee',
    departments: Array.isArray(row.departments) ? row.departments : ['general'],
  };
}

/**
 * Фабрика middleware для проверки сессии с возможностью подмены pool.
 */
export function createAuthMiddleware(poolProvider: () => pg.Pool = getPool) {
  return async (req: Request, res: Response, next: NextFunction): Promise<void> => {
    const sessionId = req.cookies?.[SESSION_COOKIE_NAME];
    if (!sessionId) {
      res.status(401).json({ detail: 'Необходима авторизация' });
      return;
    }

    try {
      const user = await getSessionUser(poolProvider(), sessionId);
      if (!user) {
        res.status(401).json({ detail: 'Недействительная или истекшая сессия' });
        return;
      }

      (req as AuthenticatedRequest).user = user;
      next();
    } catch (err: any) {
      console.error('[Auth] Error validating session:', err.message);
      res.status(500).json({ detail: 'Ошибка проверки сессии' });
    }
  };
}

/**
 * Стандартный Express middleware авторизации.
 */
export const authMiddleware = createAuthMiddleware(getPool);
