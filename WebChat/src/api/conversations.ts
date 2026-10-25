import { apiFetch } from './client';
import type { Conversation, Message } from '../types';

export async function getConversations(): Promise<Conversation[]> {
  const data = await apiFetch<{ conversations: Conversation[] }>('/api/conversations', {
    method: 'GET',
  });
  return data.conversations || [];
}

export async function createConversation(title?: string): Promise<Conversation> {
  const data = await apiFetch<{ conversation: Conversation }>('/api/conversations', {
    method: 'POST',
    body: JSON.stringify(title ? { title } : {}),
  });
  return data.conversation;
}

export async function getConversation(id: string): Promise<{ conversation: Conversation; messages: Message[] }> {
  const data = await apiFetch<{ conversation: Conversation; messages: Message[] }>(`/api/conversations/${id}`, {
    method: 'GET',
  });
  return data;
}

export async function deleteConversation(id: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/api/conversations/${id}`, {
    method: 'DELETE',
  });
}
