import { memo, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, RefreshCw, Sun, Moon, Search, CircleCheck, CircleAlert, ArrowDown } from 'lucide-react';
import { api, agentName, dateTime, seconds, stageNames, diagnosticTabs, matchesTab, type DiagnosticTab, type Review, type Trace, type TraceSummary } from './types';
import { TraceDetail } from './TraceDetail';

const Summary = memo(function Summary({ items }: { items: TraceSummary[] }) {
  const durations = items.map(item => item.duration_ms).sort((a, b) => a - b);
  const reviewed = items.filter(item => item.review.stage);
  const correct = reviewed.filter(item => item.review.stage === 'correct').length;
  const stages = Object.entries(stageNames).filter(([key]) => !['correct', 'unknown'].includes(key));
  return <section className="summary" aria-label="Сводка по отфильтрованным запросам">
    <div><span>Запросы / ошибки</span><strong>{items.length} <span className="divider">/</span> {items.filter(item => item.status === 'error').length}</strong></div>
    <div><span>Время p50 / p95</span><strong>{durations.length ? `${seconds(durations[Math.floor((durations.length - 1) * .5)])} / ${seconds(durations[Math.ceil(durations.length * .95) - 1])}` : 'Нет данных'}</strong></div>
    <div><span>Верных среди разобранных</span><strong>{reviewed.length ? `${correct} из ${reviewed.length}` : 'Ещё нет оценок'}</strong></div>
    <div className="breakdown"><span>Отмеченные причины</span>{stages.map(([key, label]) => <span key={key}>{label}: {reviewed.filter(item => item.review.stage === key).length}</span>)}</div>
  </section>;
});

const TraceRow = memo(function TraceRow({ item, selected, onChoose }: { item: TraceSummary; selected: boolean; onChoose: (id: string) => void }) {
  return <button className="trace-row" aria-pressed={selected} onClick={() => onChoose(item.id)}>
        <span className="row-meta"><span>{agentName(item.agent)}</span><time dateTime={item.started_at}>{dateTime(item.started_at)}</time></span><strong>{item.question}</strong>
        <span className="row-state"><span>{seconds(item.duration_ms)}{!['ocr', 'search'].includes(item.agent) ? ` · поиск ${item.searches} · модель ${item.model_calls}` : ''}</span>{item.status === 'error' ? <CircleAlert size={14} className="bad" aria-label="Ошибка выполнения" /> : <CircleCheck size={14} className="muted" aria-label="Выполнен" />}</span>
        {!['ocr', 'search'].includes(item.agent) ? <span className={`review-state ${item.review.stage === 'correct' ? 'good' : ''}`}>{item.review.stage ? stageNames[item.review.stage] : 'Без оценки'}{item.review.score ? ` · ${item.review.score}/5` : ''}</span> : null}
      </button>;
});

export function App() {
  const [tab, setTab] = useState<DiagnosticTab>('agents');
  const [items, setItems] = useState<TraceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);
  const [agent, setAgent] = useState('');
  const [status, setStatus] = useState('');
  const [stage, setStage] = useState('');
  const [selected, setSelected] = useState('');
  const [trace, setTrace] = useState<Trace | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [visible, setVisible] = useState(40);
  const [auto, setAuto] = useState(false);
  const [dark, setDark] = useState(() => {
    try { return localStorage.getItem('viio-diagnostics-theme-v1') === 'dark'; } catch { return false; }
  });
  const request = useRef<AbortController | null>(null);
  useEffect(() => { document.documentElement.classList.toggle('dark', dark); try { localStorage.setItem('viio-diagnostics-theme-v1', dark ? 'dark' : 'light'); } catch { /* Theme still works without storage. */ } }, [dark]);
  const refresh = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setLoading(true); setError('');
    try {
      const result = await api<{ items: TraceSummary[]; retention: string }>('/api/traces', { signal: controller.signal });
      if (controller.signal.aborted) return;
      setItems(result.items);
    } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Не удалось загрузить журнал. Повторите запрос.'); }
    finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => { void refresh(); return () => request.current?.abort(); }, [refresh]);
  useEffect(() => {
    if (!auto) return;
    const timer = setInterval(() => {
      if (!document.hidden && !request.current) void refresh();
    }, 10000);
    return () => clearInterval(timer);
  }, [auto, refresh]);
  const [detailRevision, setDetailRevision] = useState(0);
  useEffect(() => {
    setTrace(null); setDetailError('');
    if (!selected) return;
    const controller = new AbortController(); setDetailLoading(true);
    api<Trace>(`/api/traces/${selected}`, { signal: controller.signal }).then(result => { if (!controller.signal.aborted) setTrace(result); })
      .catch(err => { if (!controller.signal.aborted) setDetailError(err.message); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selected, detailRevision]);
  const filtered = useMemo(() => {
    const q = deferredQuery.trim().toLocaleLowerCase('ru');
    return items.filter(item => matchesTab(item, tab) && (!q || `${item.question} ${item.id}`.toLocaleLowerCase('ru').includes(q)) &&
      (tab !== 'agents' || !agent || item.agent === agent) && (!status || item.status === status) &&
      (tab !== 'agents' || !stage || (stage === 'unreviewed' ? !item.review.stage : item.review.stage === stage)));
  }, [items, deferredQuery, agent, status, stage, tab]);
  useEffect(() => { setVisible(40); }, [deferredQuery, agent, status, stage, tab]);
  const changeTab = (next: DiagnosticTab) => {
    setTab(next);
    const current = items.find(item => item.id === selected);
    if (current && !matchesTab(current, next)) setSelected('');
  };
  const saved = useCallback((id: string, review: Review) => {
    setItems(old => old.map(item => item.id === id ? { ...item, review } : item));
    setTrace(old => old?.id === id ? { ...old, review } : old);
  }, []);
  const choose = useCallback((id: string) => {
    setSelected(id);
    if (window.matchMedia('(max-width: 760px)').matches) document.getElementById('detail')?.scrollIntoView({ block: 'start' });
  }, []);
  return <>
    <a className="skip" href="#detail">К разбору ответа</a>
    <header><div className="brand">VIIO <span>Диагностика</span></div><button className="theme" onClick={() => setDark(value => !value)} aria-label={dark ? 'Светлая тема' : 'Тёмная тема'}>{dark ? <Sun size={17} /> : <Moon size={17} />}</button></header>
    <main>
    {error ? <div className="error" role="alert">{error} <button onClick={() => void refresh()}>Повторить</button></div> : null}
    <div className="diagnostic-toolbar"><nav className="diagnostic-tabs" aria-label="Раздел диагностики">{diagnosticTabs.map(item => <button key={item.id} aria-current={tab === item.id ? 'page' : undefined} onClick={() => changeTab(item.id)}>{item.label}<span>{items.filter(trace => matchesTab(trace, item.id)).length}</span></button>)}</nav><div className="refresh-group"><label className="checkbox"><input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} />Автообновление</label><button onClick={() => void refresh()} disabled={loading}><RefreshCw size={16} className={loading ? 'spin' : ''} aria-hidden="true" />Обновить</button></div></div>
    {tab === 'agents' ? <Summary items={filtered} /> : null}
    <form className="filters" onSubmit={event => event.preventDefault()}><label className="query">Поиск<div className="search-input"><Search size={17} aria-hidden="true" /><input type="search" placeholder="Текст или номер обращения" value={query} onChange={e => setQuery(e.target.value)} /></div></label>
      {tab === 'agents' ? <label>Агент<select value={agent} onChange={e => setAgent(e.target.value)}><option value="">Оба агента</option><option value="main">Основной</option><option value="consultant">Консультант</option></select></label> : null}
      <label>Результат<select value={status} onChange={e => setStatus(e.target.value)}><option value="">Все результаты</option><option value="error">Ошибка выполнения</option><option value="ok">Выполнен</option></select></label>
      {tab === 'agents' ? <label>Разбор<select value={stage} onChange={e => setStage(e.target.value)}><option value="">Все запросы</option><option value="unreviewed">Без оценки</option>{Object.entries(stageNames).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label> : null}
    </form>
    <div className="workspace"><section className="journal" aria-label="Журнал запросов" aria-busy={loading}><div className="journal-heading"><h2>Запросы <span>{filtered.length}</span></h2><span><ArrowDown size={12} aria-hidden="true" />Новые сверху</span></div>
      <div className="trace-list">{loading && !items.length ? <div className="empty" role="status"><Activity size={24} /><p>Загружаем журнал…</p></div> : filtered.length ? filtered.slice(0, visible).map(item => <TraceRow key={item.id} item={item} selected={selected === item.id} onChoose={choose} />) : <div className="empty"><h3>{items.length ? 'Ничего не найдено' : 'Пока нет запросов'}</h3><p>{items.length ? 'Измените фильтры или текст поиска.' : 'Задайте вопрос любому агенту. Завершённый запрос появится здесь после обновления журнала.'}</p></div>}</div>
      {filtered.length > visible ? <div className="load-more"><button onClick={() => setVisible(value => value + 40)}>Показать ещё</button></div> : null}
    </section><section id="detail" className="detail" aria-label="Разбор выбранного запроса" aria-busy={detailLoading} tabIndex={-1}>
      {detailLoading ? <div className="empty" role="status">Загружаем этапы запроса…</div> : detailError ? <div className="error" role="alert">{detailError}<button onClick={() => setDetailRevision(value => value + 1)}>Повторить</button></div> : trace ? <TraceDetail key={trace.id} trace={trace} items={items} onSaved={saved} tab={tab} /> : <div className="empty detail-empty"><Activity size={32} aria-hidden="true" /><h2>Выберите обращение</h2></div>}
    </section></div>
    </main>
  </>;
}
