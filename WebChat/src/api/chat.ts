import { apiFetch } from './client';
import type { ChatResponse } from '../types';

export interface RagOptions {
  rag_backend: 'local' | 'yandex';
  confirmed_public_for_external_cloud: boolean;
}

export async function sendMessage(conversationId: string, message: string, options?: RagOptions): Promise<ChatResponse> {
  return await apiFetch<ChatResponse>('/api/chat', {
    method: 'POST',
    body: JSON.stringify({
      conversation_id: conversationId,
      message,
      ...options,
    }),
  });
}
