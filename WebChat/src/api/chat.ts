import { apiFetch } from './client';
import type { ChatResponse } from '../types';

export async function sendMessage(conversationId: string, message: string): Promise<ChatResponse> {
  return await apiFetch<ChatResponse>('/api/chat', {
    method: 'POST',
    body: JSON.stringify({
      conversation_id: conversationId,
      message,
    }),
  });
}
