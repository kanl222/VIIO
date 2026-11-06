import { apiFetch } from './client';
import type { User } from '../types';

export interface AdminUser extends User { created_at: string }
export interface Overview {
  counts: { users: number; conversations: number; messages: number; sessions: number };
  database: boolean;
  agent: { healthy: boolean; model?: string; provider?: string; chunks?: number };
  documents: { healthy: boolean };
  usage?: { input_tokens: number; output_tokens: number; total_tokens: number;
    reported_calls: number; unreported_calls: number; started_at: string } | null;
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
export const extractPdf = (file: File) => apiFetch<{ content: string; pages: number; engine: string }>(
  '/api/admin/documents/extract-pdf', { method: 'POST', headers: { 'Content-Type': 'application/pdf' }, body: file }
);
export const extractOffice = (file: File) => apiFetch<{ content: string; pages: null; engine: string }>(
  `/api/admin/documents/extract-office?filename=${encodeURIComponent(file.name)}`,
  { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file }
);

export interface CatalogDocument {
  id: string;
  title: string;
  mime_type: string;
  file_size?: number | null;
  sha256?: string | null;
  owner_id?: string | null;
  version: number;
  family_id?: string;
  is_active: boolean;
  document_kind?: string;
  original_filename?: string;
  status: string;
  is_public: boolean;
  access_class?: 'public' | 'restricted';
  allowed_roles: string[];
  allowed_departments: string[];
  has_original: boolean;
  chunks_count: number;
  created_at: string;
  updated_at: string;
}

export interface CatalogListResult {
  items: CatalogDocument[];
  total: number;
  page: number;
  page_size: number;
}

export const listDocuments = (page = 1, pageSize = 20, status = '', signal?: AbortSignal) => {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (status) query.set('status', status);
  return apiFetch<CatalogListResult>(`/api/admin/documents/catalog?${query.toString()}`, { signal });
};

export const downloadDocument = async (docId: string): Promise<Blob> => {
  const response = await fetch(`/api/admin/documents/download/${encodeURIComponent(docId)}`);
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(errorData.detail || 'Не удалось скачать файл.');
  }
  return response.blob();
};

export interface BulkUploadTaskResult {
  task_id: string | null;
  filename: string;
  status: string;
  document_id?: string;
  chunks_count?: number;
  error?: string;
}

export const bulkUpload = async (
  files: File[],
  options: { is_public: boolean; allowed_roles: string[]; allowed_departments: string[]; version_parent_id?: string }
): Promise<{ tasks: BulkUploadTaskResult[] }> => {
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file);
  }
  const query = new URLSearchParams({
    is_public: String(options.is_public),
    allowed_roles: options.allowed_roles.join(','),
    allowed_departments: options.allowed_departments.join(','),
  });
  if (options.version_parent_id) query.set('version_parent_id', options.version_parent_id);

  const response = await fetch(`/api/admin/documents/bulk?${query.toString()}`, {
    method: 'POST',
    body: formData,
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(err.detail || 'Не удалось отправить файлы на обработку.');
  }
  return response.json();
};

export const getBulkStatus = (taskIds: string[]) =>
  apiFetch<{ tasks: BulkUploadTaskResult[] }>(`/api/admin/documents/bulk-status?task_ids=${encodeURIComponent(taskIds.join(','))}`);

export const retryBulk = (taskId: string) => apiFetch(`/api/admin/documents/bulk-retry/${encodeURIComponent(taskId)}`, { method: 'POST' });

export interface UploadReview {
  task_id: string;
  filename: string;
  content: string;
  token: string;
  engine: string;
  pages: number;
}
export const getUploadReview = (taskId: string) => apiFetch<UploadReview>(`/api/admin/documents/bulk-review/${encodeURIComponent(taskId)}`);
export const approveUploadReview = (review: UploadReview) => apiFetch(`/api/admin/documents/bulk-review/${encodeURIComponent(review.task_id)}`, {
  method: 'POST', body: JSON.stringify({ content: review.content, review_token: review.token }),
});
export const cancelBulk = (taskId: string) => apiFetch(`/api/admin/documents/bulk-cancel/${encodeURIComponent(taskId)}`, { method: 'POST' });
export const documentVersions = (docId: string, signal?: AbortSignal) => apiFetch<{ items: CatalogDocument[] }>(`/api/admin/documents/versions/${encodeURIComponent(docId)}`, { signal });

export interface Publication { title: string; content: string; published_at?: string }
export const getPublication = (id: string, signal?: AbortSignal) => apiFetch<{ publication: Publication | null }>(`/api/admin/documents/publication/${encodeURIComponent(id)}`, { signal });
export const publishDocument = (id: string, publication: Publication) => apiFetch(`/api/admin/documents/publication/${encodeURIComponent(id)}`, { method: 'POST', body: JSON.stringify(publication) });
export const withdrawPublication = (id: string) => apiFetch(`/api/admin/documents/publication/${encodeURIComponent(id)}`, { method: 'DELETE' });
