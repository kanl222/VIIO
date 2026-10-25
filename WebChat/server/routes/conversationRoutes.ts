import { Router, type Response } from 'express';
import { v4 as uuidv4 } from 'uuid';
import type pg from 'pg';
import { getPool } from '../db.js';
import {
  authMiddleware as defaultAuthMiddleware,
  createAuthMiddleware,
  type AuthenticatedRequest,
} from '../auth.js';

export function createConversationRouter(
  poolProvider: () => pg.Pool = getPool,
  authMw?: any
): Router {
  const router = Router();
  const middleware = authMw || createAuthMiddleware(poolProvider);

  // Все маршруты диалогов защищены авторизацией
  router.use(middleware);

  // GET /api/conversations - список диалогов текущего пользователя
  router.get('/', async (req: AuthenticatedRequest, res: Response): Promise<void> => {
    const userId = req.user?.id;
    if (!userId) {
      res.status(401).json({ detail: 'Необходима авторизация.' });
      return;
    }

    const pool = poolProvider();
    try {
      const result = await pool.query(
        `SELECT id, title, agent_thread_id, created_at, updated_at
         FROM conversations
         WHERE user_id = $1
         ORDER BY updated_at DESC`,
        [userId]
      );

      res.json({ conversations: result.rows });
    } catch (err: any) {
      console.error('[Conversations] Error listing conversations:', err.message);
      res.status(500).json({ detail: 'Ошибка при получении списка диалогов.' });
    }
  });

  // POST /api/conversations - создание нового диалога
  router.post('/', async (req: AuthenticatedRequest, res: Response): Promise<void> => {
    const userId = req.user?.id;
    if (!userId) {
      res.status(401).json({ detail: 'Необходима авторизация.' });
      return;
    }

    const rawTitle = req.body?.title;
    const title = typeof rawTitle === 'string' && rawTitle.trim() ? rawTitle.trim() : 'Новый диалог';
    const agentThreadId = uuidv4();
    const pool = poolProvider();

    try {
      const result = await pool.query(
        `INSERT INTO conversations (user_id, title, agent_thread_id)
         VALUES ($1, $2, $3)
         RETURNING id, title, agent_thread_id, created_at, updated_at`,
        [userId, title, agentThreadId]
      );

      const conv = result.rows[0];
      res.status(201).json({
        conversation: {
          id: conv.id,
          title: conv.title,
          agent_thread_id: conv.agent_thread_id,
          created_at: conv.created_at,
          updated_at: conv.updated_at,
        },
      });
    } catch (err: any) {
      console.error('[Conversations] Error creating conversation:', err.message);
      res.status(500).json({ detail: 'Ошибка при создании диалога.' });
    }
  });

  // GET /api/conversations/:id - детали диалога и история сообщений
  router.get('/:id', async (req: AuthenticatedRequest, res: Response): Promise<void> => {
    const userId = req.user?.id;
    const conversationId = req.params.id;

    if (!userId) {
      res.status(401).json({ detail: 'Необходима авторизация.' });
      return;
    }

    const pool = poolProvider();
    try {
      const convResult = await pool.query(
        `SELECT id, title, agent_thread_id, created_at, updated_at
         FROM conversations
         WHERE id = $1 AND user_id = $2`,
        [conversationId, userId]
      );

      if (convResult.rows.length === 0) {
        res.status(404).json({ detail: 'Диалог не найден.' });
        return;
      }

      const msgResult = await pool.query(
        `SELECT id, sender, text, attachments, created_at
         FROM messages
         WHERE conversation_id = $1
         ORDER BY created_at ASC`,
        [conversationId]
      );

      res.json({
        conversation: convResult.rows[0],
        messages: msgResult.rows,
      });
    } catch (err: any) {
      console.error('[Conversations] Error fetching conversation:', err.message);
      res.status(500).json({ detail: 'Ошибка при загрузке диалога.' });
    }
  });

  // DELETE /api/conversations/:id - удаление диалога (каскадно удаляет сообщения)
  router.delete('/:id', async (req: AuthenticatedRequest, res: Response): Promise<void> => {
    const userId = req.user?.id;
    const conversationId = req.params.id;

    if (!userId) {
      res.status(401).json({ detail: 'Необходима авторизация.' });
      return;
    }

    const pool = poolProvider();
    try {
      const result = await pool.query(
        `DELETE FROM conversations
         WHERE id = $1 AND user_id = $2
         RETURNING id`,
        [conversationId, userId]
      );

      if (result.rows.length === 0) {
        res.status(404).json({ detail: 'Диалог не найден.' });
        return;
      }

      res.json({ ok: true });
    } catch (err: any) {
      console.error('[Conversations] Error deleting conversation:', err.message);
      res.status(500).json({ detail: 'Ошибка при удалении диалога.' });
    }
  });

  return router;
}

export const conversationRoutes = createConversationRouter(getPool);
