export interface Review {
  stage: string; score: number | null; expected_answer: string;
  evidence: string; document_id: string; notes: string;
}
export const blankReview: Review = { stage: '', score: null, expected_answer: '', evidence: '', document_id: '', notes: '' };
export interface Fragment {
  document_id?: string; title: string; text?: string; version?: string | number; chunk_id?: string;
  filename?: string; line_start?: number; chunk_index?: number; section_path?: string | string[];
  term_score?: number; rrf_score?: number; score?: number; score_rank?: number;
  diversity_rank?: number; final_rank?: number | null; deferred_for_diversity?: boolean;
  matched_text?: string[]; matched_title?: string[]; matched_filename?: string[];
  branch_ranks?: Record<string, number[]>; selection_reason?: string; context_position?: number;
  cross_encoder_score?: number; reranker_model?: string; reranker_rank?: number;
  has_window?: boolean; window_chars?: number;
}
export interface ModelMessage { role: string; content: unknown; tool_calls?: unknown[] }
export interface TraceEvent {
  engine?: string; filename?: string; pages?: number; document?: unknown; size_bytes?: number; sha256?: string;
  languages?: string[]; max_pages?: number; max_chars?: number; timeout_seconds?: number; status_code?: number;
  prompt_sha256?: string;
  scope?: string; note?: string; candidate_count?: number;
  stage: string; title?: string; span_id?: string; parent_span_id?: string; elapsed_ms: number; duration_ms?: number; status?: string;
  query?: string; context?: string; candidates?: Fragment[]; selected?: Fragment[];
  branches?: Record<string, { status: string; count: number | null; duration_ms?: number; error?: string }>;
  search_trace_id?: string; lookup_mode?: string; requested_documents?: number;
  ranking_details?: {
    query_terms: string[]; min_score: number; rrf_k: number; text_weight: number;
    title_or_filename_weight: number; before_access_check: number; after_access_check: number;
    lost_in_the_middle_reordered?: boolean; reordered_count?: number;
  };
  ranking?: string; k?: number; model?: string; messages?: ModelMessage[];
  content?: unknown; tool_calls?: unknown[]; mode?: string; model_first_tools?: boolean;
  tools?: string[]; name?: string; error?: string; reply?: string;
  sources?: { title: string; document_id?: string; publication_id?: string }[];
  detail?: string; call_id?: string;
  specialist?: string; role?: string; branch?: string; supervisor?: boolean;
  question?: string; sql?: string; plan?: string; plan_snippet?: string;
  schema?: string; tables?: string[]; old_sql?: string; fixed_sql?: string;
  result?: string; valid?: boolean; attempt?: number; attempts?: number;
  chars?: number; raw_chars?: number; reference?: string; task?: string; answer?: string;
  relevant?: boolean; grounded?: boolean; old_query?: string; new_query?: string;
  fast_path?: boolean; active?: string; length?: number; user?: string;
  filtered_chunks?: number[]; total_chunks?: number; noise_removed?: number;
  eval_output?: string; method?: string; strategy?: string;
  draft_snippet?: string; draft_chars?: number; context_chars?: number;
  faithfulness_score?: number; lost_in_the_middle_reordered?: boolean; reordered_count?: number;
  reranker_used?: string; reranker_model?: string; cache_backend?: string;
  reranker_status?: 'disabled' | 'skipped' | 'lexical_fallback' | 'applied';
  cache_status?: 'disabled' | 'skipped' | 'miss' | 'hit';
  from_cache?: boolean; candidate_pool_size?: number;
  usage?: {
    input_tokens?: number; prompt_tokens?: number;
    output_tokens?: number; completion_tokens?: number;
    total_tokens?: number;
  };
  [key: string]: unknown;
}
export interface TraceSummary {
  id: string; agent: string; question: string; thread_id?: string; started_at: string;
  duration_ms: number; status: string; error?: string; searches: number;
  model_calls: number; event_count: number; total_tokens?: number | null; review: Partial<Review>;
  payload_truncated?: boolean;
}
export interface Trace extends TraceSummary { events: TraceEvent[]; configuration?: Record<string, string>; payload_truncated?: boolean; }
export const stageNames: Record<string, string> = {
  correct: 'Ответ верный',
  route: 'Маршрут',
  retrieval: 'Поиск',
  selection: 'Отбор фрагментов',
  context: 'Контекст модели',
  generation: 'Формулирование',
  database: 'База данных / SQL',
  coding: 'Кодер / разработка',
  ocr: 'Распознавание PDF',
  guard: 'Проверка безопасности',
  unknown: 'Причина не установлена',
};
export type DiagnosticTab = 'agents' | 'ocr' | 'search';
export const diagnosticTabs: { id: DiagnosticTab; label: string }[] = [
  { id: 'agents', label: 'Агенты' }, { id: 'ocr', label: 'OCR' }, { id: 'search', label: 'Поиск' },
];
export const matchesTab = (item: TraceSummary, tab: DiagnosticTab) => tab === 'agents'
  ? !['ocr', 'search'].includes(item.agent) : item.agent === (tab === 'search' ? 'search' : 'ocr');
export const agentName = (value: string) => ({ main: 'Основной', consultant: 'Консультант', ocr: 'Распознавание PDF', search: 'Поиск документов' }[value] || value);
export const seconds = (ms: number) => `${(ms / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} с`;
const dateFormatter = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });
export const dateTime = (value: string) => dateFormatter.format(new Date(value));
export const text = (value: unknown): string => typeof value === 'string' ? value : JSON.stringify(value, null, 2) || '';
export async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Не удалось загрузить данные. Повторите запрос.');
  return result as T;
}
