export interface Review {
  stage: string; score: number | null; expected_answer: string;
  evidence: string; document_id: string; notes: string;
}
export const blankReview: Review = { stage: '', score: null, expected_answer: '', evidence: '', document_id: '', notes: '' };
export interface Fragment { document_id?: string; title: string; text: string; version?: string | number; chunk_id?: string; line_start?: number }
export interface ModelMessage { role: string; content: unknown; tool_calls?: unknown[] }
export interface TraceEvent {
  prompt_sha256?: string;
  scope?: string; note?: string; candidate_count?: number;
  stage: string; elapsed_ms: number; duration_ms?: number; status?: string;
  query?: string; context?: string; candidates?: Fragment[]; selected?: Fragment[];
  branches?: Record<string, { status: string; count: number | null }>;
  ranking?: string; k?: number; model?: string; messages?: ModelMessage[];
  content?: unknown; tool_calls?: unknown[]; mode?: string; model_first_tools?: boolean;
  tools?: string[]; name?: string; error?: string; reply?: string;
  sources?: { title: string; document_id?: string; publication_id?: string }[];
  detail?: string; call_id?: string;
}
export interface TraceSummary {
  id: string; agent: string; question: string; thread_id?: string; started_at: string;
  duration_ms: number; status: string; error?: string; searches: number;
  model_calls: number; event_count: number; review: Partial<Review>;
}
export interface Trace extends TraceSummary { events: TraceEvent[]; configuration?: Record<string, string> }
export const stageNames: Record<string, string> = {
  correct: 'Ответ верный', route: 'Маршрут', retrieval: 'Поиск', selection: 'Отбор фрагментов',
  context: 'Контекст модели', generation: 'Формулирование', unknown: 'Причина не установлена',
};
export const agentName = (value: string) => value === 'main' ? 'Основной' : value === 'consultant' ? 'Консультант' : value;
export const seconds = (ms: number) => `${(ms / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} с`;
export const dateTime = (value: string) => new Date(value).toLocaleString('ru-RU', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });
export const text = (value: unknown): string => typeof value === 'string' ? value : JSON.stringify(value, null, 2) || '';
export async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Не удалось загрузить данные. Повторите запрос.');
  return result as T;
}
