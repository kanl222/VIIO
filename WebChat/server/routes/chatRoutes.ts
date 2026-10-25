import { Router, type Response } from 'express';
import type pg from 'pg';
import { getPool } from '../db.js';
import {
  authMiddleware as defaultAuthMiddleware,
  createAuthMiddleware,
  type AuthenticatedRequest,
} from '../auth.js';

export function createChatRouter(
  poolProvider: () => pg.Pool = getPool,
  authMw?: any,
  fetchFn: typeof fetch = fetch,
  agentUrlProvider: () => string = () => process.env.AGENTAI_URL || 'http://127.0.0.1:8000'
): Router {
  const router = Router();
  const middleware = authMw || createAuthMiddleware(poolProvider);

  // Защита маршрута отправки сообщений
  router.use(middleware);

  // POST /api/chat - отправка сообщения в диалог и получение ответа агента
  router.post('/', async (req: AuthenticatedRequest, res: Response): Promise<void> => {
    const userId = req.user?.id;
    if (!userId) {
      res.status(401).json({ detail: 'Необходима авторизация.' });
      return;
    }

    const { conversation_id, message, attachments } = req.body || {};

    if (!conversation_id || typeof conversation_id !== 'string') {
      res.status(400).json({ detail: 'Не указан идентификатор диалога (conversation_id).' });
      return;
    }

    if (!message || typeof message !== 'string' || !message.trim()) {
      res.status(400).json({ detail: 'Текст сообщения не может быть пустым.' });
      return;
    }

    const cleanMessage = message.trim();
    const cleanAttachments = Array.isArray(attachments) ? attachments : [];
    const pool = poolProvider();

    try {
      // 1. Проверяем существование диалога и права пользователя
      const convResult = await pool.query(
        'SELECT id, agent_thread_id FROM conversations WHERE id = $1 AND user_id = $2',
        [conversation_id, userId]
      );

      if (convResult.rows.length === 0) {
        res.status(404).json({ detail: 'Диалог не найден.' });
        return;
      }

      const conversation = convResult.rows[0];

      // 2. Сохраняем сообщение пользователя в БД
      await pool.query(
        `INSERT INTO messages (conversation_id, sender, text, attachments)
         VALUES ($1, $2, $3, $4)`,
        [conversation_id, 'user', cleanMessage, JSON.stringify(cleanAttachments)]
      );

      // 3. Вызываем сервис диалогового агента APIAgent
      const agentBaseUrl = agentUrlProvider().replace(/\/+$/, '');
      const agentChatUrl = `${agentBaseUrl}/api/v1/chat`;
      const internalApiKey = process.env.INTERNAL_API_KEY || 'viio-internal-secret-token';

      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      if (internalApiKey) {
        headers['X-Internal-Token'] = internalApiKey;
      }

      const userContext = {
        user_id: req.user?.id,
        role: req.user?.role || 'employee',
        departments: req.user?.departments || ['general'],
      };

      let agentResponse: globalThis.Response;
      try {
        agentResponse = await fetchFn(agentChatUrl, {
          method: 'POST',
          headers,
          body: JSON.stringify({
            message: cleanMessage,
            thread_id: conversation.agent_thread_id,
            user_context: userContext,
          }),
          signal: AbortSignal.timeout(120_000),
        });
      } catch (networkErr: any) {
        console.error('[Chat] Network error connecting to APIAgent:', networkErr.message);
        res.status(502).json({
          detail: 'Сервис диалогового агента APIAgent недоступен. Попробуйте позже.',
        });
        return;
      }

      if (!agentResponse.ok) {
        let errDetail = 'Ошибка при генерации ответа агентом.';
        try {
          const errData = (await agentResponse.json()) as { detail?: string } | undefined;
          if (errData && typeof errData.detail === 'string') {
            errDetail = errData.detail;
          }
        } catch {
          // ignore parsing error
        }
        res.status(agentResponse.status >= 500 ? 502 : agentResponse.status).json({
          detail: errDetail,
        });
        return;
      }

      const data = await agentResponse.json() as { reply?: string; thread_id?: string };
      const replyText = typeof data.reply === 'string' ? data.reply : '';
      const replyThreadId = data.thread_id || conversation.agent_thread_id;

      // 4. Сохраняем ответ ассистента в БД
      const assistantMsgResult = await pool.query(
        `INSERT INTO messages (conversation_id, sender, text, attachments)
         VALUES ($1, $2, $3, $4)
         RETURNING id, created_at`,
        [conversation_id, 'assistant', replyText, '[]']
      );

      // 5. Обновляем время активности диалога
      await pool.query(
        'UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = $1',
        [conversation_id]
      );

      res.json({
        reply: replyText,
        thread_id: replyThreadId,
        message_id: assistantMsgResult.rows[0].id,
      });
    } catch (err: any) {
      console.error('[Chat] Error processing chat message:', err.message);
      res.status(500).json({ detail: 'Внутренняя ошибка сервера при обработке сообщения.' });
    }
  });

  return router;
}

export const chatRoutes = createChatRouter(getPool);
