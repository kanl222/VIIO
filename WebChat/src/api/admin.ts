import { apiFetch } from './client';
import type { User } from '../types';

export interface AdminUser extends User { created_at: string }
export interface Overview {
  counts: { users: number; conversations: number; messages: number; sessions: number };
  database: boolean;
  agent: { healthy: boolean; model?: string; provider?: string; chunks?: number };
  checked_at: string;
}
export const getOverview = (signal?: AbortSignal) => apiFetch<Overview>('/api/admin/overview', { signal });
export const getUsers = (search = '', page = 1, signal?: AbortSignal) =>
  apiFetch<{ users: AdminUser[]; total: number; page: number; page_size: number }>(
    `/api/admin/users?search=${encodeURIComponent(search)}&page=${page}`, { signal }
  );
export const updateUser = (id: string, role: string, departments: string[]) =>
  apiFetch<{ user: AdminUser }>(`/api/admin/users/${encodeURIComponent(id)}`, {
    method: 'PATCH', body: JSON.stringify({ role, departments }),
  });
export const addDocument = (document: {
  title: string; content: string; is_public: boolean; allowed_roles: string[]; allowed_departments: string[];
}) => apiFetch<{ document_id: string; title: string; chunks_count: number }>('/api/admin/documents', {
  method: 'POST', body: JSON.stringify(document),
});
