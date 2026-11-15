import { stageNames, text } from '../types';

export const scoreLabel = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 4 });

const dateFormatter = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

export function dateTime(value: string): string {
  try {
    return dateFormatter.format(new Date(value));
  } catch {
    return value;
  }
}

export function seconds(ms: number): string {
  if (ms < 1000) return `${ms} мс`;
  return `${(ms / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} с`;
}

export function formatTokens(tokens: number | null | undefined): string {
  if (tokens === null || tokens === undefined) return '';
  return tokens.toLocaleString('ru-RU');
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '';
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

export const agentName = (value: string): string => {
  const map: Record<string, string> = {
    main: 'Основной',
    consultant: 'Консультант',
    ocr: 'Распознавание PDF',
    search: 'Поиск документов',
  };
  return map[value] || value;
};

export const eventNames: Record<string, string> = {
  tool_start: 'Запуск инструмента',
  tool_end: 'Завершение инструмента',
  route: 'Выбор маршрута супервизором',
  specialist_dispatch: 'Передача ветке / специалисту',
  supervisor_review: 'Итоговая проверка супервизора',
  retrieval: 'Поиск и отбор фрагментов',
  graph_search: 'Поиск по графу знаний (Memgraph)',
  context: 'Вход модели',
  generation: 'Результат модели',
  guard: 'Проверка доступа',
  answer: 'Ответ пользователю',
  capture_limit: 'Ограничение журнала',
  // База данных (Database Analyst Subgraph)
  db_inspect_schema: 'БД: Инспекция схемы и таблиц',
  db_optimize_query: 'БД: Анализ и оптимизация запроса',
  db_diagnose_error: 'БД: Диагностика ошибки в логах',
  db_generate_sql: 'БД: Генерация SQL-запроса',
  db_explain_check: 'БД: Проверка плана EXPLAIN',
  db_fix_sql: 'БД: Исправление SQL (Fix Loop)',
  db_execute_sql: 'БД: Выполнение SQL в PostgreSQL',
  db_format_answer: 'БД: Форматирование ответа',
  // Кодер (Coding Specialist)
  coding_dispatch: 'Кодер: Классификация задачи',
  coding_reference: 'Кодер: Справка и код из БЗ',
  coding_generation: 'Кодер: Разработка решения',
  // CRAG (Corrective RAG Subgraph)
  crag_retrieve: 'CRAG: Извлечение документов',
  crag_grade_documents: 'CRAG: Оценка релевантности',
  crag_rewrite_query: 'CRAG: Переформулирование запроса',
  crag_generate: 'CRAG: Генерация черновика',
  crag_check_groundedness: 'CRAG: Проверка обоснованности (Groundedness)',
  crag_fallback: 'CRAG: Резервный ответ',
  document_catalog: 'Каталог документов',
  ocr_request: 'Параметры распознавания PDF',
  ocr_result: 'Результат распознавания PDF',
  search_request: 'Обращение к поиску',
};

export const getEventName = (event: { stage: string; supervisor?: boolean; title?: string }): string => {
  if (event.title) return event.title;
  if (event.stage === 'route') {
    return event.supervisor ? 'Выбор маршрута супервизором' : 'Маршрутизация запроса (Единый агент)';
  }
  return eventNames[event.stage] || event.stage;
};

export const roleNames: Record<string, string> = {
  human: 'Пользователь',
  user: 'Пользователь',
  ai: 'Модель',
  assistant: 'Модель',
  system: 'Системное сообщение',
  tool: 'Результат инструмента',
};

export const searchBranchNames: Record<string, string> = {
  semantic: 'По смыслу (Dense)',
  sparse_bm25: 'BM25 (Sparse)',
  lexical: 'По словам',
  public: 'Публичный индекс',
};

export async function copyToClipboard(content: string): Promise<boolean> {
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(content);
      return true;
    }
  } catch {
    // fallback below
  }
  try {
    const textarea = document.createElement('textarea');
    textarea.value = content;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const successful = document.execCommand('copy');
    document.body.removeChild(textarea);
    return successful;
  } catch {
    return false;
  }
}
