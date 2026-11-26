import { useState, useEffect, useCallback, useRef } from 'react';
import type { Message } from '../types';
import * as convApi from '../api/conversations';
import * as chatApi from '../api/chat';

export function useChat(
  conversationId: string | null,
  onConversationUpdated?: () => void
) {
  const [history, setHistory] = useState<{ id: string | null; messages: Message[] }>({
    id: conversationId, messages: [],
  });
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<chatApi.TaskStep[]>([]);
  const [progressText, setProgressText] = useState('');
  const activeId = useRef(conversationId);
  const loadVersion = useRef(0);
  const pendingSend = useRef<{ id: string; messages: Message[] } | null>(null);
  const activeRequest = useRef<AbortController | null>(null);
  const historyRef = useRef(history);
  historyRef.current = history;
  const onConversationUpdatedRef = useRef(onConversationUpdated);
  onConversationUpdatedRef.current = onConversationUpdated;

  activeId.current = conversationId;

  // Загрузка сообщений выбранного диалога
  const loadMessages = useCallback(async () => {
    setPlan([]);
    setProgressText('');
    const version = ++loadVersion.current;
    const isCurrent = () => activeId.current === conversationId && loadVersion.current === version;
    if (!conversationId) {
      setHistory({ id: null, messages: [] });
      setLoading(false);
      setError(null);
      return;
    }
    // В только что выбранном диалоге первое сообщение уже может быть в процессе отправки.
    if (pendingSend.current?.id === conversationId) {
      setHistory(pendingSend.current);
      setLoading(false);
      setError(null);
      return;
    }

    setHistory({ id: conversationId, messages: [] });
    setLoading(true);
    setError(null);
    try {
      const data = await convApi.getConversation(conversationId);
      if (isCurrent()) setHistory({ id: conversationId, messages: data.messages || [] });
    } catch (err: any) {
      if (isCurrent()) {
        setError(err.message || 'Ошибка загрузки сообщений');
        setHistory({ id: conversationId, messages: [] });
      }
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [conversationId]);

  useEffect(() => {
    loadMessages();
    return () => { ++loadVersion.current; };
  }, [loadMessages]);

  // Отправка сообщения
  const sendMessage = useCallback(
    async (
      text: string,
      targetId: string | null = conversationId,
      options?: import('../api/chat').RagOptions,
      attachments?: import('../types').MessageAttachment[]
    ) => {
      if (!targetId || !text.trim() || pendingSend.current) {
        return;
      }
      ++loadVersion.current;
      // Создание и первая отправка происходят до того, как React зафиксирует новый выбор.
      if (!conversationId) activeId.current = targetId;
      const isCurrent = () => activeId.current === targetId;

      const trimmedText = text.trim();
      const tempUserId = `temp-${Date.now()}`;
      const userMessage: Message = {
        id: tempUserId,
        conversation_id: targetId,
        sender: 'user',
        text: trimmedText,
        attachments: attachments && attachments.length > 0 ? attachments : undefined,
        created_at: new Date().toISOString(),
      };

      const curHistory = historyRef.current;
      const pending = {
        id: targetId,
        messages: [...(curHistory.id === targetId ? curHistory.messages : []), userMessage],
      };
      pendingSend.current = pending;
      setHistory(pending);
      setLoading(false);
      setSubmitting(true);
      setError(null);

      try {
        const controller = new AbortController();
        activeRequest.current = controller;
        setPlan([]);
        setProgressText('Обрабатываем запрос…');
        let partial = '';
        const streamingId = `stream-${Date.now()}`;
        const response = await chatApi.sendMessage(
          targetId,
          trimmedText,
          options,
          event => {
            if (!isCurrent()) return;
            if (event.type === 'plan') setPlan(event.steps);
            if (event.type === 'status') setProgressText(event.message);
            if (event.type === 'reset') {
              partial = '';
              setHistory(pending);
            }
            if (event.type === 'delta') {
              partial += event.text;
              setProgressText('Формируем ответ…');
              setHistory({ id: targetId, messages: [...pending.messages, {
                id: streamingId, conversation_id: targetId, sender: 'assistant', text: partial,
                created_at: new Date().toISOString(),
              }] });
            }
          },
          attachments,
          controller.signal
        );
        const assistantMessage: Message = {
          id: response.message_id || `asst-${Date.now()}`,
          conversation_id: targetId,
          sender: 'assistant',
          text: response.reply,
          attachments: (response.sources || []).map(source => ({ type: 'knowledge_source', name: source.title, data: { document_id: source.document_id } })),
          created_at: new Date().toISOString(),
        };

        if (isCurrent()) setHistory({ id: targetId, messages: [...pending.messages, assistantMessage] });

        if (onConversationUpdatedRef.current) {
          onConversationUpdatedRef.current();
        }
      } catch (err: any) {
        const errMsg = err.name === 'AbortError' ? 'Запрос остановлен пользователем.' : err.message || 'Не удалось получить ответ от агента';
        if (isCurrent()) setError(errMsg);
        // Добавляем системное сообщение об ошибке в чат
        const errorSystemMessage: Message = {
          id: `err-${Date.now()}`,
          conversation_id: targetId,
          sender: 'system',
          text: `⚠️ Ошибка: ${errMsg}`,
          created_at: new Date().toISOString(),
        };
        if (isCurrent()) setHistory({ id: targetId, messages: [...pending.messages, errorSystemMessage] });
      } finally {
        activeRequest.current = null;
        setProgressText('');
        pendingSend.current = null;
        setSubmitting(false);
      }
    },
    [conversationId]
  );

  return {
    messages: history.id === conversationId ? history.messages : [],
    loading,
    submitting,
    plan,
    progressText,
    error,
    sendMessage,
    cancelRequest: () => activeRequest.current?.abort(),
    reloadMessages: loadMessages,
  };
}
