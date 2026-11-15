import { apiFetch } from './client';
import type { ChatResponse, MessageAttachment } from '../types';
import { ApiRequestError } from './client';
import { readEventStream } from '../../server/eventStream';

export interface TaskStep { title: string; status: 'pending' | 'in_progress' | 'completed' }
export type ChatStreamEvent = { type: 'delta'; text: string } | { type: 'reset' }
  | { type: 'status'; message: string } | { type: 'plan'; steps: TaskStep[] };

export interface RagOptions {
  rag_backend: 'local' | 'yandex';
  confirmed_public_for_external_cloud: boolean;
}

export async function sendMessage(
  conversationId: string,
  message: string,
  options?: RagOptions,
  onEvent?: (event: ChatStreamEvent) => void,
  attachments?: MessageAttachment[]
): Promise<ChatResponse> {
  const payload = {
    conversation_id: conversationId,
    message,
    ...options,
    ...(attachments && attachments.length > 0 ? { attachments } : {}),
  };

  if (onEvent) {
    const response = await fetch('/api/chat', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ ...payload, stream: true }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new ApiRequestError(response.status, error.detail || 'Не удалось получить ответ.');
    }
    for await (const event of readEventStream(response)) {
      if (event.type === 'done') return event as unknown as ChatResponse;
      if (event.type === 'error') throw new Error(event.detail || 'Поток ответа прерван.');
      if (['delta', 'reset', 'status', 'plan'].includes(event.type)) onEvent(event as ChatStreamEvent);
    }
    throw new Error('Поток завершился без подтверждённого ответа.');
  }
  return await apiFetch<ChatResponse>('/api/chat', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
