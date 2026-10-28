import { useEffect, useState, type FormEvent } from 'react';
import { Download, Save, ChevronDown, Search, FileCheck, MessageSquare, ShieldCheck, ArrowRight } from 'lucide-react';
import { api, agentName, blankReview, dateTime, seconds, stageNames, text, type Fragment, type Review, type Trace, type TraceEvent, type TraceSummary } from './types';

const eventNames: Record<string, string> = { route: 'Выбор маршрута', retrieval: 'Поиск и отбор фрагментов', context: 'Вход модели', generation: 'Результат модели', guard: 'Проверка доступа', answer: 'Ответ пользователю', capture_limit: 'Ограничение журнала' };
const roleNames: Record<string, string> = { human: 'Пользователь', user: 'Пользователь', ai: 'Модель', assistant: 'Модель', system: 'Системное сообщение', tool: 'Результат инструмента' };

function Fragments({ items, label }: { items: Fragment[] | undefined; label: string }) {
  return <section className="fragments"><h3>{label} {items ? <span className="muted">({items.length})</span> : null}</h3>
    <div className="fragment-list">{items === undefined ? <p className="hint">Этот этап не записан в трассе.</p> : items.length === 0 ? <p className="hint">Ничего не найдено.</p> : items.map((item, index) => <details className="fragment" key={`${item.document_id}-${index}`}>
      <summary><span className="rank">{index + 1}</span>{item.title || 'Без названия'}</summary>
      <div className="fragment-meta">{item.document_id || 'Идентификатор не записан'}{item.version ? ` · версия ${item.version}` : ''}{item.chunk_id ? ` · фрагмент ${item.chunk_id}` : ''}{item.line_start ? ` · со строки ${item.line_start}` : ''}</div>
      <div className="text fragment-text">{item.text}</div>
    </details>)}</div></section>;
}

function EventBody({ event }: { event: TraceEvent }) {
  if (event.stage === 'retrieval') return <>
    <p><strong>Запрос:</strong> {event.query}</p>
    {event.status === 'error' ? <p className="bad">Поиск завершился ошибкой.</p> : null}
    {event.branches ? <div className="branch-list">{Object.entries(event.branches).map(([name, value]) => <span key={name} className={value.status === 'error' ? 'bad' : ''}>{name}: {value.status === 'error' ? 'ошибка' : `${value.count} результатов до итоговой проверки доступа`}</span>)}</div> : null}
    <p className="hint">{event.ranking ? `Порядок отбора: ${event.ranking}.` : ''} {event.k ? `Лимит: ${event.k}.` : ''} Кандидаты содержат только разрешённые материалы; порядок списка кандидатов — до итогового ранжирования.{event.scope === 'publication_gateway' ? ` Векторных кандидатов до повторной проверки публикаций: ${event.candidate_count ?? 'не записано'}.` : ''}</p>
    <div className="retrieval-fragments"><Fragments items={event.candidates} label="Кандидаты поиска" /><Fragments items={event.selected} label="Отобранные фрагменты" /></div>
    {event.context !== undefined ? <details><summary>Результат, переданный агенту</summary><div className="text fragment-text">{event.context}</div></details> : null}
  </>;
  if (event.stage === 'context') return <><p className="hint">Модель: {event.model || 'Не записана'}. Это фактические сообщения, переданные при данном вызове.</p>{event.prompt_sha256 ? <p className="fragment-meta">SHA-256 системного промпта: {event.prompt_sha256}</p> : null}
    {event.messages?.map((message, index) => <div className="model-message" key={index}><h3>{roleNames[message.role] || message.role}</h3><div className="text">{text(message.content)}</div>{message.tool_calls?.length ? <pre>{text(message.tool_calls)}</pre> : null}</div>)}
  </>;
  if (event.stage === 'generation') return <>{event.error ? <p className="bad">Ошибка: {event.error}</p> : <div className="text">{text(event.content)}</div>}{event.tool_calls?.length ? <><h3 className="section-title">Запрошенные инструменты</h3><pre>{text(event.tool_calls)}</pre></> : null}<p className="hint">Показан видимый результат вызова. Внутренние рассуждения модели не записываются.</p></>;
  if (event.stage === 'answer') return <><div className="text">{event.reply}</div><SourceList event={event} /></>;
  if (event.stage === 'route') return <dl className="route-data"><dt>Режим</dt><dd>{event.mode}</dd><dt>Доступные инструменты</dt><dd>{event.tools?.join(', ') || 'Без инструментов'}</dd>{event.model_first_tools !== undefined ? <><dt>Выбор инструмента</dt><dd>{event.model_first_tools ? 'Сначала модель' : 'По правилам приложения'}</dd></> : null}</dl>;
  if (event.stage === 'guard') return <p className={event.status === 'blocked' ? 'bad' : 'good'}>{event.name}: {event.status === 'blocked' ? 'ответ заблокирован' : 'проверка пройдена'}</p>;
  if (event.stage === 'error') return <p className="bad">{event.detail}: {event.error}</p>;
  return <p>{event.detail || 'Данные этапа не записаны.'}</p>;
}

function SourceList({ event }: { event: TraceEvent | undefined }) {
  return event?.sources?.length ? <p className="hint">Источники ответа: {event.sources.map(source => source.title).join('; ')}</p> : <p className="hint">Источники к ответу не приложены.</p>;
}

function EvidenceCheck({ trace, review }: { trace: Trace; review: Review }) {
  const needle = review.evidence.trim().toLocaleLowerCase('ru');
  const documentId = review.document_id.trim();
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
}

function ReviewPanel({ trace, onSaved }: { trace: Trace; onSaved: (id: string, review: Review) => void }) {
  const [review, setReview] = useState<Review>({ ...blankReview, ...trace.review });
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
      <div className="wide"><EvidenceCheck trace={trace} review={review} /></div>
      <label className="wide">Что пошло не так<textarea maxLength={6000} value={review.notes} onChange={e => change('notes', e.target.value)} placeholder="Наблюдения и следующий шаг проверки" /></label>
    </div><div className="review-actions"><button className="primary" type="submit" disabled={saving}><Save size={16} aria-hidden="true" />{saving ? 'Сохраняем…' : 'Сохранить разбор'}</button><span role="status">{message}</span></div></fieldset>{error ? <p className="error" role="alert">{error}</p> : null}</form>
  </section>;
}

function ComparePanel({ trace, items }: { trace: Trace; items: TraceSummary[] }) {
  const peers = items.filter(item => item.id !== trace.id && item.question.trim().toLocaleLowerCase('ru') === trace.question.trim().toLocaleLowerCase('ru'));
  const [selected, setSelected] = useState('');
  const [other, setOther] = useState<Trace | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    setOther(null); setError('');
    if (!selected) return;
    const controller = new AbortController(); setLoading(true);
    api<Trace>(`/api/traces/${selected}`, { signal: controller.signal }).then(setOther)
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

export function TraceDetail({ trace, items, onSaved }: { trace: Trace; items: TraceSummary[]; onSaved: (id: string, review: Review) => void }) {
  const [answerExpanded, setAnswerExpanded] = useState(false);
  const answer = [...trace.events].reverse().find(event => event.stage === 'answer');
  const exportTrace = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(trace, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `viio-trace-${trace.id}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const jump = (stage: string) => {
    const target = document.getElementById(`${trace.id}-${trace.events.findIndex(event => event.stage === stage)}`) as HTMLDetailsElement | null;
    if (target) { target.open = true; target.scrollIntoView({ block: 'start' }); target.querySelector('summary')?.focus(); }
  };
  return <>
    <div className="detail-head"><h2>{trace.question}</h2><button onClick={exportTrace} title="Скачать выбранную трассу в JSON"><Download size={16} aria-hidden="true" />JSON</button></div>
    <div className="metadata">{agentName(trace.agent)} · {dateTime(trace.started_at)} · {seconds(trace.duration_ms)}<br />Трасса {trace.id}{trace.thread_id ? <> · Диалог {trace.thread_id}</> : null}</div>
    {trace.configuration && Object.keys(trace.configuration).length ? <details><summary>Параметры запуска</summary><pre>{text(trace.configuration)}</pre></details> : null}
    {trace.status === 'error' ? <div className="error" role="status">Запрос завершился ошибкой: {trace.error || 'тип не записан'}. Сохранены этапы, выполненные до сбоя.</div> : null}
    <h2>Цепочка ответа</h2><p className="hint">Время отсчитывается от начала запроса. Откройте этап, чтобы увидеть его данные.</p>
    <nav className="pipeline" aria-label="Перейти к этапу">{[
      { stage: 'retrieval', title: 'Поиск и отбор', icon: Search }, { stage: 'context', title: 'Контекст', icon: FileCheck },
      { stage: 'generation', title: 'Модель', icon: MessageSquare }, { stage: 'answer', title: 'Ответ', icon: ShieldCheck },
    ].map(({ stage, title, icon: Icon }) => <button key={stage} onClick={() => jump(stage)} disabled={!trace.events.some(event => event.stage === stage)}><Icon size={16} aria-hidden="true" />{title}<span>{trace.events.filter(event => event.stage === stage).length}</span></button>)}</nav>
    <div className="events">{trace.events.map((event, index) => <details className="event" key={index} id={`${trace.id}-${index}`}>
      <summary><span className="event-number">{index + 1}</span><span>{event.scope === 'publication_gateway' ? 'Поиск опубликованных материалов' : eventNames[event.stage] || event.stage}{event.status === 'error' || event.status === 'blocked' ? <span className="bad"> · {event.status === 'blocked' ? 'блокировка' : 'ошибка'}</span> : null}</span><small>+{seconds(event.elapsed_ms)}{event.duration_ms !== undefined ? ` · ${seconds(event.duration_ms)}` : ''}</small><ChevronDown size={15} aria-hidden="true" /></summary>
      <div className="event-body"><EventBody event={event} /></div>
    </details>)}</div>
    <section className="answer"><h3>Ответ пользователю</h3><div className={`text ${!answerExpanded && (answer?.reply?.length || 0) > 700 ? 'answer-preview' : ''}`}>{answer?.reply || 'Ответ в трассе отсутствует.'}</div>{(answer?.reply?.length || 0) > 700 ? <button className="answer-toggle" onClick={() => setAnswerExpanded(value => !value)} aria-expanded={answerExpanded}>{answerExpanded ? 'Свернуть ответ' : 'Читать ответ полностью'}</button> : null}<SourceList event={answer} /></section>
    <ReviewPanel trace={trace} onSaved={onSaved} /><ComparePanel trace={trace} items={items} />
    <p className="hint trace-end"><ArrowRight size={14} aria-hidden="true" />Причина ошибки определяется по данным трассы и эталону. Журнал не назначает её автоматически.</p>
  </>;
}
