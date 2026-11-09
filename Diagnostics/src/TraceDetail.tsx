import { memo, useDeferredValue, useEffect, useMemo, useState, type FormEvent } from 'react';
import { Download, Save, ChevronDown, Search, FileCheck, MessageSquare, ShieldCheck, ArrowRight, Database, Code, GitBranch } from 'lucide-react';
import { api, agentName, blankReview, dateTime, seconds, stageNames, text, type DiagnosticTab, type Fragment, type Review, type Trace, type TraceEvent, type TraceSummary } from './types';

import { LazyDetails } from './LazyDetails';

const eventNames: Record<string, string> = {
  route: 'Выбор маршрута супервизором',
  specialist_dispatch: 'Передача ветке / специалисту',
  supervisor_review: 'Итоговая проверка супервизора',
  retrieval: 'Поиск и отбор фрагментов',
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
};
const roleNames: Record<string, string> = { human: 'Пользователь', user: 'Пользователь', ai: 'Модель', assistant: 'Модель', system: 'Системное сообщение', tool: 'Результат инструмента' };

function Fragments({ items, label }: { items: Fragment[] | undefined; label: string }) {
  return <section className="fragments"><h3>{label} {items ? <span className="muted">({items.length})</span> : null}</h3>
    <div className="fragment-list">{items === undefined ? <p className="hint">Этот этап не записан в трассе.</p> : items.length === 0 ? <p className="hint">Ничего не найдено.</p> : items.map((item, index) => <LazyDetails className="fragment" key={`${item.document_id}-${index}`} summary={<><span className="rank">{index + 1}</span>{item.title || 'Без названия'}</>}>
      {() => <>
      <div className="fragment-meta">{item.document_id || 'Идентификатор не записан'}{item.version ? ` · версия ${item.version}` : ''}{item.chunk_id ? ` · фрагмент ${item.chunk_id}` : ''}{item.line_start ? ` · со строки ${item.line_start}` : ''}</div>
      <div className="text fragment-text">{item.text}</div></>}
    </LazyDetails>)}</div></section>;
}

const EventBody = memo(function EventBody({ event }: { event: TraceEvent }) {
  if (event.stage === 'ocr_request') return <dl className="route-data"><dt>Обработчик</dt><dd>{event.engine || 'Не записан'}</dd><dt>Размер файла</dt><dd>{event.size_bytes?.toLocaleString('ru-RU')} байт</dd><dt>Языки OCR</dt><dd>{event.languages?.join(', ')}</dd><dt>Лимит страниц</dt><dd>{event.max_pages}</dd><dt>Лимит символов</dt><dd>{event.max_chars}</dd><dt>Таймаут</dt><dd>{event.timeout_seconds} с</dd><dt>SHA-256 оригинала</dt><dd className="file-digest">{event.sha256}</dd></dl>;
  if (event.stage === 'ocr_result') return <>
    <p><strong>Результат:</strong> {event.status === 'error' ? 'Ошибка обработки' : 'Распознано'}{event.engine ? ` · ${event.engine}` : ''}{event.pages !== undefined ? ` · страниц: ${event.pages}` : ''}</p>
    {event.error ? <p className="bad">{event.status_code ? `${event.status_code}: ` : ''}{event.detail || event.error}</p> : null}
    {event.content !== undefined ? <><h3>Распознанный текст</h3><div className="text fragment-text">{text(event.content)}</div></> : null}
    {event.document !== undefined ? <LazyDetails summary="Структура документа: страницы, элементы и таблицы (JSON)">{() => <pre>{text(event.document)}</pre>}</LazyDetails> : <p className="hint">Структура документа не записана.</p>}
  </>;
  if (event.stage === 'search_request') return <><p><strong>Запрос:</strong> {event.query}</p><p className="hint">Область: {event.scope === 'publication_gateway' ? 'Одобренные публикации' : 'Доступные пользователю документы'}.{event.k !== undefined ? ` Лимит: ${event.k}.` : ''}</p></>;
  if (event.stage === 'retrieval') return <>
    <p><strong>Запрос:</strong> {event.query}</p>
    {event.status === 'error' ? <p className="bad">Поиск завершился ошибкой.</p> : null}
    {event.branches ? <div className="branch-list">{Object.entries(event.branches).map(([name, value]) => <span key={name} className={value.status === 'error' ? 'bad' : ''}>{name}: {value.status === 'error' ? 'ошибка' : `${value.count} результатов до итоговой проверки доступа`}</span>)}</div> : null}
    <p className="hint">{event.ranking ? `Порядок отбора: ${event.ranking}.` : ''} {event.k ? `Лимит: ${event.k}.` : ''} Кандидаты содержат только разрешённые материалы; порядок списка кандидатов — до итогового ранжирования.{event.scope === 'publication_gateway' ? ` Векторных кандидатов до повторной проверки публикаций: ${event.candidate_count ?? 'не записано'}.` : ''}</p>
    <div className="retrieval-fragments"><Fragments items={event.candidates} label="Кандидаты поиска" /><Fragments items={event.selected} label="Отобранные фрагменты" /></div>
    {event.context !== undefined ? <LazyDetails summary="Результат, переданный агенту">{() => <div className="text fragment-text">{event.context}</div>}</LazyDetails> : null}
  </>;
  if (event.stage === 'context') return <><p className="hint">Модель: {event.model || 'Не записана'}. Это фактические сообщения, переданные при данном вызове.</p>{event.prompt_sha256 ? <p className="fragment-meta">SHA-256 системного промпта: {event.prompt_sha256}</p> : null}
    {event.messages?.map((message, index) => <div className="model-message" key={index}><h3>{roleNames[message.role] || message.role}</h3><div className="text">{text(message.content)}</div>{message.tool_calls?.length ? <pre>{text(message.tool_calls)}</pre> : null}</div>)}
  </>;
  if (event.stage === 'generation') {
    const usage = event.usage;
    const promptTokens = usage?.input_tokens ?? usage?.prompt_tokens;
    const completionTokens = usage?.output_tokens ?? usage?.completion_tokens;
    const totalTokens = usage?.total_tokens ?? ((promptTokens ?? 0) + (completionTokens ?? 0) || undefined);
    return <>
      {event.error ? <p className="bad">Ошибка: {event.error}</p> : <div className="text">{text(event.content)}</div>}
      {event.tool_calls?.length ? <><h3 className="section-title">Запрошенные инструменты</h3><pre>{text(event.tool_calls)}</pre></> : null}
      {totalTokens !== undefined ? <p className="hint">Использовано токенов: {totalTokens.toLocaleString('ru-RU')}{promptTokens !== undefined && completionTokens !== undefined ? ` (вход: ${promptTokens.toLocaleString('ru-RU')}, выход: ${completionTokens.toLocaleString('ru-RU')})` : ''}</p> : null}
      <p className="hint">Показан видимый результат вызова. Внутренние рассуждения модели не записываются.</p>
    </>;
  }
  if (event.stage === 'answer') return <><div className="text">{event.reply}</div><SourceList event={event} /></>;
  if (event.stage === 'route') return <dl className="route-data">
    <dt>Режим</dt><dd><strong>{event.mode}</strong>{event.branch ? ` (ветка: ${event.branch})` : ''}</dd>
    <dt>Доступные инструменты</dt><dd>{event.tools?.join(', ') || 'Без инструментов'}</dd>
    {event.model_first_tools !== undefined ? <><dt>Выбор инструмента</dt><dd>{event.model_first_tools ? 'Сначала модель' : 'По правилам приложения'}</dd></> : null}
  </dl>;
  if (event.stage === 'specialist_dispatch') return <div className="specialist-box">
    <p><strong>Целевая ветка:</strong> <span className="badge-highlight">{event.name || event.specialist}</span></p>
    {event.question ? <p className="hint">Входящий запрос: {event.question}</p> : null}
    {event.result ? <p className="good"><strong>Результат:</strong> {event.result}</p> : null}
  </div>;

  if (event.stage === 'db_inspect_schema') return <>
    <p><strong>Таблицы:</strong> {event.tables?.length ? event.tables.join(', ') : 'Не указаны'}</p>
    <p className="hint">Объем схемы: {event.length?.toLocaleString('ru-RU')} символов</p>
    {event.schema ? <LazyDetails summary="Схема выбранных таблиц">{() => <pre className="code-block">{event.schema}</pre>}</LazyDetails> : null}
  </>;

  if (event.stage === 'db_optimize_query') return <>
    <p><strong>Запрос на оптимизацию:</strong></p>
    {event.sql ? <pre className="code-block"><code>{event.sql}</code></pre> : null}
    <h3>План выполнения (EXPLAIN)</h3>
    <pre className="code-block">{event.plan || 'План не получен'}</pre>
    {event.tables?.length ? <p className="hint">Таблицы: {event.tables.join(', ')}</p> : null}
  </>;

  if (event.stage === 'db_diagnose_error') return <>
    <p className="bad"><strong>Диагностика ошибки:</strong> {event.error || 'Ошибка не указана'}</p>
    {event.tables?.length ? <p className="hint">Связанные таблицы: {event.tables.join(', ')}</p> : null}
  </>;

  if (event.stage === 'db_generate_sql') return <>
    <p><strong>Сгенерированный SQL-запрос:</strong></p>
    <pre className="code-block"><code>{event.sql || event.query}</code></pre>
    {event.tables?.length ? <p className="hint">Целевые таблицы: {event.tables.join(', ')}</p> : null}
  </>;

  if (event.stage === 'db_explain_check') return <>
    <p><strong>Статус проверки плана: </strong><span className={event.valid ? 'good' : 'bad'}>{event.valid ? '✓ План запроса валиден (Explain Check OK)' : '✗ План некорректен (Требуется автоисправление)'}</span></p>
    {event.sql ? <pre className="code-block"><code>{event.sql}</code></pre> : null}
    {event.error ? <p className="bad"><strong>Ошибка PostgreSQL:</strong> {event.error}</p> : null}
    {(event.plan || event.plan_snippet) ? <LazyDetails summary="План выполнения запроса (EXPLAIN)">{() => <pre className="code-block">{event.plan || event.plan_snippet}</pre>}</LazyDetails> : null}
  </>;

  if (event.stage === 'db_fix_sql') return <>
    <p className="bad"><strong>Цикл автоисправления SQL (попытка #{event.attempt})</strong></p>
    {event.error ? <p className="bad">Причина: {event.error}</p> : null}
    <div className="diff-view">
      <div><h4>Было (ошибочный SQL):</h4><pre className="code-block"><code>{event.old_sql}</code></pre></div>
      <div><h4>Стало (исправленный SQL):</h4><pre className="code-block"><code>{event.fixed_sql}</code></pre></div>
    </div>
  </>;

  if (event.stage === 'db_execute_sql') return <>
    <p className="good"><strong>SQL выполнен успешно.</strong> Длина результата: {event.length?.toLocaleString('ru-RU')} байт</p>
    {event.sql ? <pre className="code-block"><code>{event.sql}</code></pre> : null}
    {event.result ? <LazyDetails summary="Фрагмент возвращенных данных">{() => <pre className="code-block">{event.result}</pre>}</LazyDetails> : null}
  </>;

  if (event.stage === 'db_format_answer') return <>
    <p><strong>Форматирование ответа:</strong> {event.length?.toLocaleString('ru-RU')} символов</p>
    {event.answer ? <LazyDetails summary="Сформированный ответ">{() => <div className="text">{event.answer}</div>}</LazyDetails> : null}
  </>;

  if (event.stage === 'coding_dispatch') return <>
    <p><strong>Задача программирования:</strong> {event.task}</p>
    {event.user ? <p className="hint">Пользователь: {event.user}</p> : null}
  </>;

  if (event.stage === 'coding_reference') return <>
    <p><strong>Поиск по базе знаний / коду:</strong> {event.query || 'Без запроса'}</p>
    <p className="hint">Статус: {event.status === 'ok' ? `Найдено ${event.chars?.toLocaleString('ru-RU')} символов` : 'Контекст не найден'}</p>
    {event.reference ? <LazyDetails summary="Справочный фрагмент кода/документа">{() => <pre className="code-block">{event.reference}</pre>}</LazyDetails> : null}
  </>;

  if (event.stage === 'coding_generation') return <>
    <p><strong>Ответ программиста сформирован:</strong> {event.length?.toLocaleString('ru-RU')} символов</p>
    {event.answer ? <LazyDetails summary="Текст решения кодера">{() => <div className="text">{event.answer}</div>}</LazyDetails> : null}
  </>;

  if (event.stage === 'crag_retrieve') return <>
    <p><strong>Извлечение документов (CRAG):</strong> {event.query}</p>
    <p className="hint">Режим: {event.mode || 'векторный'} · Объем: {event.chars ?? event.length ?? 0} символов</p>
  </>;

  if (event.stage === 'crag_grade_documents') return <>
    <p><strong>Оценка релевантности: </strong><span className={event.relevant ? 'good' : 'bad'}>{event.relevant ? '✓ Документы релевантны вопросу' : '✗ Документы признаны нерелевантными'}</span>{event.fast_path ? ' (быстрый путь)' : ''}</p>
    {event.query ? <p className="hint">Запрос: {event.query}</p> : null}
  </>;

  if (event.stage === 'crag_rewrite_query') return <>
    <p className="bad"><strong>Переформулирование запроса (попытка #{event.attempt})</strong></p>
    <dl className="route-data"><dt>Было</dt><dd>{event.old_query}</dd><dt>Стало</dt><dd><strong>{event.new_query}</strong></dd></dl>
  </>;

  if (event.stage === 'crag_check_groundedness') return <>
    <p><strong>Проверка обоснованности источниками (Groundedness): </strong><span className={event.grounded ? 'good' : 'bad'}>{event.grounded ? '✓ Ответ полностью подтвержден источниками' : '✗ Обнаружена галлюцинация / нехватка фактов'}</span></p>
  </>;

  if (event.stage === 'crag_fallback') return <p className="bad">Переход к резервному ответу после {event.attempts} попыток переформулирования.</p>;

  if (event.stage === 'supervisor_review') return <>
    <p><strong>Итоговая проверка ответа супервизором.</strong></p>
    {event.active ? <p className="hint">Активный специалист: {event.active}</p> : null}
    {event.sources?.length ? <p className="hint">Подтверждающие источники: {event.sources.join(', ')}</p> : null}
  </>;

  if (event.stage === 'guard') return <p className={event.status === 'blocked' ? 'bad' : 'good'}>{event.name}: {event.status === 'blocked' ? 'ответ заблокирован' : 'проверка пройдена'}</p>;
  if (event.stage === 'error') return <p className="bad">{event.detail}: {event.error}</p>;
  return <p>{event.detail || 'Данные этапа не записаны.'}</p>;
});

function SourceList({ event }: { event: TraceEvent | undefined }) {
  return event?.sources?.length ? <p className="hint">Источники ответа: {event.sources.map(source => source.title).join('; ')}</p> : <p className="hint">Источники к ответу не приложены.</p>;
}

const EvidenceCheck = memo(function EvidenceCheck({ trace, evidence, documentId: rawId }: { trace: Trace; evidence: string; documentId: string }) {
  const needle = evidence.trim().toLocaleLowerCase('ru');
  const documentId = rawId.trim();
  if (!needle && !documentId) return <p className="hint">Укажите точную фразу из инструкции или идентификатор документа, чтобы проверить, на каких этапах они присутствуют.</p>;
  const searches = trace.events.filter(event => event.stage === 'retrieval');
  const hasPhrase = (value: unknown) => text(value).toLocaleLowerCase('ru').includes(needle);
  const fragmentsCheck = (field: 'candidates' | 'selected') => {
    if (!searches.length || searches.some(event => !event[field])) return null;
    return searches.some(event => event[field]?.some(doc => (!documentId || doc.document_id === documentId) && (!needle || hasPhrase(doc.text))));
  };
  const contexts = trace.events.filter(event => event.stage === 'context');
  const answers = trace.events.filter(event => event.stage === 'answer');
  const values: [string, boolean | null][] = [
    ['Кандидаты', fragmentsCheck('candidates')], ['Отбор', fragmentsCheck('selected')],
    ['Вход модели', needle && contexts.length ? contexts.some(event => hasPhrase(event.messages?.map(message => text(message.content)).join('\n'))) : null],
    ['Ответ', needle && answers.length ? answers.some(event => hasPhrase(event.reply)) : null],
  ];
  return <><div className="evidence-grid">{values.map(([label, value]) => <span key={label} className={value === true ? 'good' : value === false ? 'bad' : ''}>{label}: {value === true ? 'найдено' : value === false ? 'не найдено' : 'нет данных'}</span>)}</div><p className="hint">Проверяется буквальное вхождение фразы без учёта регистра. Это подсказка для разбора, а не оценка смысла или правильности ответа. В кандидатах и отборе учитывается также указанный документ.</p></>;
});

function ReviewPanel({ trace, onSaved }: { trace: Trace; onSaved: (id: string, review: Review) => void }) {
  const [review, setReview] = useState<Review>({ ...blankReview, ...trace.review });
  const evidence = useDeferredValue(review.evidence);
  const documentId = useDeferredValue(review.document_id);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const change = <K extends keyof Review>(key: K, value: Review[K]) => { setReview(old => ({ ...old, [key]: value })); setMessage(''); };
  const save = async (event: FormEvent) => {
    event.preventDefault(); setSaving(true); setMessage(''); setError('');
    try {
      const saved = await api<Review>(`/api/reviews/${trace.id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(review) });
      onSaved(trace.id, saved); setMessage('Разбор сохранён');
    } catch (err) { setError(err instanceof Error ? err.message : 'Не удалось сохранить. Повторите попытку.'); }
    finally { setSaving(false); }
  };
  return <section className="review"><h2>Сравнение с эталоном</h2><p className="hint">Оцените ответ и отметьте первый этап, на котором потерялись нужные сведения. Эталон хранится только в этом журнале и не передаётся агентам.</p>
    <form onSubmit={save}><fieldset disabled={saving}><div className="review-grid">
      <label>Результат разбора<select value={review.stage} onChange={e => change('stage', e.target.value)}><option value="">Без оценки</option>{Object.entries(stageNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
      <label>Качество ответа<select value={review.score ?? ''} onChange={e => change('score', e.target.value ? Number(e.target.value) : null)}><option value="">Не оценено</option>{[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n} / 5</option>)}</select></label>
      <label className="wide">Ожидаемый ответ<textarea maxLength={6000} value={review.expected_answer} onChange={e => change('expected_answer', e.target.value)} placeholder="Что должен был ответить агент и на какие сведения опереться" /></label>
      <label>Точная фраза из источника<textarea maxLength={6000} value={review.evidence} onChange={e => change('evidence', e.target.value)} placeholder="Фраза, наличие которой проверяем" /></label>
      <label>Идентификатор нужного документа<input maxLength={64} value={review.document_id} onChange={e => change('document_id', e.target.value)} placeholder="Скопируйте из кандидатов поиска" /><span className="hint">Необязательно. Для консультанта — ID публикации.</span></label>
      <div className="wide"><EvidenceCheck trace={trace} evidence={evidence} documentId={documentId} /></div>
      <label className="wide">Что пошло не так<textarea maxLength={6000} value={review.notes} onChange={e => change('notes', e.target.value)} placeholder="Наблюдения и следующий шаг проверки" /></label>
    </div><div className="review-actions"><button className="primary" type="submit" disabled={saving}><Save size={16} aria-hidden="true" />{saving ? 'Сохраняем…' : 'Сохранить разбор'}</button><span role="status">{message}</span></div></fieldset>{error ? <p className="error" role="alert">{error}</p> : null}</form>
  </section>;
}

function ComparePanel({ trace, items }: { trace: Trace; items: TraceSummary[] }) {
  const peers = useMemo(() => {
    const question = trace.question.trim().toLocaleLowerCase('ru');
    return items.filter(item => item.id !== trace.id && item.question.trim().toLocaleLowerCase('ru') === question);
  }, [items, trace.id, trace.question]);
  const [selected, setSelected] = useState('');
  const [other, setOther] = useState<Trace | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    setOther(null); setError('');
    if (!selected) { setLoading(false); return; }
    const controller = new AbortController(); setLoading(true);
    api<Trace>(`/api/traces/${selected}`, { signal: controller.signal }).then(result => { if (!controller.signal.aborted) setOther(result); })
      .catch(err => { if (!controller.signal.aborted) setError(err.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [selected]);
  return <section className="compare"><h2>Сравнить ответы на этот вопрос</h2>{peers.length ? <>
    <label>Другой запрос<select value={selected} onChange={e => setSelected(e.target.value)}><option value="">Выберите ответ для сравнения</option>{peers.map(peer => <option key={peer.id} value={peer.id}>{agentName(peer.agent)} · {dateTime(peer.started_at)} · {seconds(peer.duration_ms)}</option>)}</select></label>
    {loading ? <p role="status">Загружаем ответ…</p> : null}{error ? <p role="alert" className="error">{error}</p> : null}
    {other ? <div className="compare-columns">{[trace, other].map(item => <div key={item.id}><h3>{agentName(item.agent)}</h3><p className="hint">{dateTime(item.started_at)} · {seconds(item.duration_ms)}</p><div className="text">{[...item.events].reverse().find(event => event.stage === 'answer')?.reply || 'Ответ не записан'}</div></div>)}</div> : null}
  </> : <p className="hint">В журнале пока нет другого запроса с таким же текстом. Задайте этот вопрос второму агенту или повторите его после изменения системы.</p>}</section>;
}

export const TraceDetail = memo(function TraceDetail({ trace, items, onSaved, tab = 'agents' }: { trace: Trace; items: TraceSummary[]; onSaved: (id: string, review: Review) => void; tab?: DiagnosticTab }) {
  const [answerExpanded, setAnswerExpanded] = useState(false);
  const answer = useMemo(() => [...trace.events].reverse().find(event => event.stage === 'answer'), [trace.events]);
  const exportTrace = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(trace, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `viio-trace-${trace.id}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const jumpToEvent = (index: number) => {
    const target = document.getElementById(`${trace.id}-${index}`) as HTMLDetailsElement | null;
    if (target) { target.open = true; target.scrollIntoView({ block: 'start' }); target.querySelector('summary')?.focus(); }
  };
  const jump = (stage: string) => jumpToEvent(trace.events.findIndex(event => event.stage === stage));
  if (tab !== 'agents') {
    const stages = tab === 'ocr' ? ['ocr_request', 'ocr_result', 'capture_limit'] : ['search_request', 'retrieval', 'capture_limit'];
    const events = trace.events.filter(event => stages.includes(event.stage));
    return <>
      <div className="detail-head"><h2>{tab === 'ocr' ? 'Распознавание PDF' : trace.question}</h2><button onClick={exportTrace}><Download size={16} aria-hidden="true" />JSON</button></div>
      <p className="metadata">{trace.question}<br />{dateTime(trace.started_at)} · {seconds(trace.duration_ms)}<br />Обращение {trace.id}{trace.thread_id ? <><br />Задание {trace.thread_id}</> : null}</p>
      {trace.status === 'error' ? <p className="error" role="status">Обработка завершилась ошибкой: {trace.error}. Сохранены доступные данные.</p> : null}
      {events.length ? events.map((event, index) => <section className="operation-event" key={index}><h3>{event.stage === 'ocr_request' ? 'Параметры запроса' : event.stage === 'ocr_result' ? 'Ответ обработки PDF' : event.stage === 'search_request' ? 'Обращение к поиску' : event.stage === 'retrieval' ? 'Результат поиска' : 'Ограничение журнала'}</h3><p className="hint">От начала обращения: {seconds(event.elapsed_ms)}</p><EventBody event={event} /></section>) : <p className="hint">Данные этого этапа в журнале отсутствуют.</p>}
    </>;
  }
  const totalTokens = trace.events.reduce((sum, e) => {
    const u = e.usage;
    const t = u?.total_tokens ?? ((u?.input_tokens ?? u?.prompt_tokens ?? 0) + (u?.output_tokens ?? u?.completion_tokens ?? 0));
    return sum + (t || 0);
  }, 0);
  return <>
    <div className="detail-head"><h2>{trace.question}</h2><button onClick={exportTrace} title="Скачать выбранную трассу в JSON"><Download size={16} aria-hidden="true" />JSON</button></div>
    <div className="metadata">{agentName(trace.agent)} · {dateTime(trace.started_at)} · {seconds(trace.duration_ms)}{totalTokens > 0 ? ` · ${totalTokens.toLocaleString('ru-RU')} токенов` : ''}<br />Трасса {trace.id}{trace.thread_id ? <> · Диалог {trace.thread_id}</> : null}</div>
    {trace.configuration && Object.keys(trace.configuration).length ? <LazyDetails summary="Параметры запуска">{() => <pre>{text(trace.configuration)}</pre>}</LazyDetails> : null}
    {trace.status === 'error' ? <div className="error" role="status">Запрос завершился ошибкой: {trace.error || 'тип не записан'}. Сохранены этапы, выполненные до сбоя.</div> : null}
    <h2>Цепочка ответа</h2><p className="hint">Время отсчитывается от начала запроса. Откройте этап, чтобы увидеть его данные.</p>
    <section className="trace-graph" aria-label="Граф этапов проверки">
      <h3>Граф этапов и веток</h3>
      <p className="hint">Блоки соединены в порядке выполнения шагов графа. Нажмите на любой блок, чтобы раскрыть его детали.</p>
      {trace.events.length ? <ol className="trace-graph-nodes">{trace.events.map((event, index) => {
        const failed = event.stage === 'error' || event.status === 'error' || Boolean(event.error) || event.valid === false || event.relevant === false || event.grounded === false;
        const blocked = event.status === 'blocked';
        const good = event.valid === true || event.relevant === true || event.grounded === true;
        return <li key={index}><button className={`trace-graph-node${failed || blocked ? ' trace-graph-alert' : ''}`} onClick={() => jumpToEvent(index)} aria-label={`Этап ${index + 1}: ${eventNames[event.stage] || event.stage}. Открыть подробности`}>
          <span className="trace-graph-number">{index + 1}</span>
          <strong>{eventNames[event.stage] || event.stage}</strong>
          <span className="trace-graph-time">+{seconds(event.elapsed_ms)}{event.duration_ms !== undefined ? ` · длительность ${seconds(event.duration_ms)}` : ''}</span>
          <span className={failed || blocked ? 'bad' : good ? 'good' : 'muted'}>
            {blocked ? 'Заблокировано' : failed ? 'Ошибка / Не пройдено' : good ? 'Успешно' : 'Событие записано'}
          </span>
          {event.specialist ? <span className="muted">Ветка: {event.name || event.specialist}</span> : null}
          {event.tables?.length ? <span className="muted">Таблицы: {event.tables.join(', ')}</span> : null}
          {event.sql ? <span className="muted">SQL: {event.sql.slice(0, 35)}…</span> : null}
          {event.attempt !== undefined ? <span className="bad">Попытка: #{event.attempt}</span> : null}
          {event.stage === 'retrieval' ? <span className="muted">Кандидатов: {event.candidates?.length ?? 'нет данных'} · Отобрано: {event.selected?.length ?? 'нет данных'}</span> : null}
        </button></li>;
      })}</ol> : <p className="hint">В трассе нет записанных этапов.</p>}
    </section>
    <nav className="pipeline" aria-label="Перейти к этапу">{[
      { stage: 'specialist_dispatch', title: 'Ветка', icon: GitBranch },
      { stage: 'retrieval', title: 'Поиск и отбор', icon: Search },
      { stage: 'db_generate_sql', title: 'БД / SQL', icon: Database },
      { stage: 'coding_generation', title: 'Код', icon: Code },
      { stage: 'context', title: 'Контекст', icon: FileCheck },
      { stage: 'generation', title: 'Модель', icon: MessageSquare },
      { stage: 'answer', title: 'Ответ', icon: ShieldCheck },
    ].map(({ stage, title, icon: Icon }) => <button key={stage} onClick={() => jump(stage)} disabled={!trace.events.some(event => event.stage === stage || (stage === 'db_generate_sql' && event.stage.startsWith('db_')) || (stage === 'coding_generation' && event.stage.startsWith('coding_')))}><Icon size={16} aria-hidden="true" />{title}<span>{trace.events.filter(event => event.stage === stage || (stage === 'db_generate_sql' && event.stage.startsWith('db_')) || (stage === 'coding_generation' && event.stage.startsWith('coding_'))).length}</span></button>)}</nav>
    <div className="events">{trace.events.map((event, index) => <LazyDetails className="event" key={index} id={`${trace.id}-${index}`} summary={<><span className="event-number">{index + 1}</span><span>{event.scope === 'publication_gateway' ? 'Поиск опубликованных материалов' : eventNames[event.stage] || event.stage}{event.status === 'error' || event.status === 'blocked' ? <span className="bad"> · {event.status === 'blocked' ? 'блокировка' : 'ошибка'}</span> : null}</span><small>+{seconds(event.elapsed_ms)}{event.duration_ms !== undefined ? ` · ${seconds(event.duration_ms)}` : ''}</small><ChevronDown size={15} aria-hidden="true" /></>}>
      {() => <div className="event-body"><EventBody event={event} /></div>}
    </LazyDetails>)}</div>
    <section className="answer"><h3>Ответ пользователю</h3><div className={`text ${!answerExpanded && (answer?.reply?.length || 0) > 700 ? 'answer-preview' : ''}`}>{answer?.reply || 'Ответ в трассе отсутствует.'}</div>{(answer?.reply?.length || 0) > 700 ? <button className="answer-toggle" onClick={() => setAnswerExpanded(value => !value)} aria-expanded={answerExpanded}>{answerExpanded ? 'Свернуть ответ' : 'Читать ответ полностью'}</button> : null}<SourceList event={answer} /></section>
    <ReviewPanel trace={trace} onSaved={onSaved} /><ComparePanel trace={trace} items={items} />
    <p className="hint trace-end"><ArrowRight size={14} aria-hidden="true" />Причина ошибки определяется по данным трассы и эталону. Журнал не назначает её автоматически.</p>
  </>;
});
