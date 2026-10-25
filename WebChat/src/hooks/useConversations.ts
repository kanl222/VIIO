import { useState, useEffect, useCallback } from 'react';
import type { Conversation } from '../types';
import * as convApi from '../api/conversations';

export function useConversations(isAuthenticated: boolean) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchConversations = useCallback(async () => {
    if (!isAuthenticated) {
      setConversations([]);
      setCurrentId(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const list = await convApi.getConversations();
      setConversations(list);
      setCurrentId((prev) => {
        if (prev && list.some((c) => c.id === prev)) {
          return prev;
        }
        return list.length > 0 ? list[0].id : null;
      });
    } catch (err: any) {
      setError(err.message || 'Ошибка загрузки диалогов');
    } finally {
      setLoading(false);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    fetchConversations();
  }, [fetchConversations]);

  const selectConversation = useCallback((id: string | null) => {
    setCurrentId(id);
  }, []);

  const createConversation = useCallback(async (title?: string) => {
    try {
      const newConv = await convApi.createConversation(title);
      setConversations((prev) => [newConv, ...prev.filter((c) => c.id !== newConv.id)]);
      setCurrentId(newConv.id);
      return newConv;
    } catch (err: any) {
      setError(err.message || 'Ошибка создания диалога');
      throw err;
    }
  }, []);

  const deleteConversation = useCallback(async (id: string) => {
    try {
      await convApi.deleteConversation(id);
      setConversations((prev) => prev.filter((c) => c.id !== id));
      setCurrentId((prev) => {
        if (prev === id) {
          const remaining = conversations.filter((c) => c.id !== id);
          return remaining.length > 0 ? remaining[0].id : null;
        }
        return prev;
      });
    } catch (err: any) {
      setError(err.message || 'Ошибка удаления диалога');
      throw err;
    }
  }, [conversations]);

  return {
    conversations,
    currentId,
    loading,
    error,
    selectConversation,
    createConversation,
    deleteConversation,
    reloadConversations: fetchConversations,
  };
}
