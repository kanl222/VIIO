import { memo, useEffect, useMemo, useState } from 'react';
import { Columns, Loader2, ArrowRightLeft } from 'lucide-react';
import { api, type Trace, type TraceSummary } from '../../types';
import { agentName, dateTime, seconds } from '../../utils/formatters';

interface ComparePanelProps {
  trace: Trace;
  items: TraceSummary[];
}

export const ComparePanel = memo(function ComparePanel({ trace, items }: ComparePanelProps) {
  const peers = useMemo(() => {
    const question = trace.question.trim().toLocaleLowerCase('ru');
    return items.filter(
      item => item.id !== trace.id && item.question.trim().toLocaleLowerCase('ru') === question
    );
  }, [items, trace.id, trace.question]);

  const [selectedId, setSelectedId] = useState('');
  const [otherTrace, setOtherTrace] = useState<Trace | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setOtherTrace(null);
    setError('');
    if (!selectedId) {
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    setLoading(true);

    api<Trace>(`/api/traces/${selectedId}`, { signal: controller.signal })
      .then(res => {
        if (!controller.signal.aborted) setOtherTrace(res);
      })
      .catch(err => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : 'Не удалось загрузить данные для сравнения.');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [selectedId]);

  const getAnswer = (t: Trace) =>
    [...t.events].reverse().find(event => event.stage === 'answer')?.reply ||
    'Ответ не был сформирован или сохранён.';

  return (
    <section className="compare-section" aria-label="Сравнение ответов на аналогичный вопрос">
      <div className="section-header">
        <div className="title-with-icon">
          <Columns size={18} className="accent-icon" aria-hidden="true" />
          <h2>Сравнить ответы на аналогичный вопрос</h2>
        </div>
        <p className="hint">
          Сравнение текущего ответа с другими прогонами (разные агенты, версии промпта или параметры).
        </p>
      </div>

      {peers.length > 0 ? (
        <div className="compare-body">
          <div className="compare-picker">
            <label htmlFor="compare-select">
              <ArrowRightLeft size={14} aria-hidden="true" />
              <span>Выберите ответ из истории ({peers.length} доступно):</span>
            </label>
            <select
              id="compare-select"
              value={selectedId}
              onChange={e => setSelectedId(e.target.value)}
              className="select-input"
            >
              <option value="">-- Выберите сессию для сравнения --</option>
              {peers.map(peer => (
                <option key={peer.id} value={peer.id}>
                  {agentName(peer.agent)} · {dateTime(peer.started_at)} · {seconds(peer.duration_ms)}
                  {peer.review?.score ? ` · ★ ${peer.review.score}` : ''}
                </option>
              ))}
            </select>
          </div>

          {loading ? (
            <div className="compare-loading">
              <Loader2 size={18} className="spin" aria-hidden="true" />
              <span>Загружаем вторую трассу…</span>
            </div>
          ) : null}

          {error ? <p className="error" role="alert">{error}</p> : null}

          {otherTrace ? (
            <div className="compare-columns-grid">
              <div className="compare-card current-card">
                <div className="card-header">
                  <span className="agent-badge">{agentName(trace.agent)} (Текущий)</span>
                  <span className="meta-time">
                    {dateTime(trace.started_at)} · {seconds(trace.duration_ms)}
                  </span>
                </div>
                <div className="compare-content text">{getAnswer(trace)}</div>
              </div>

              <div className="compare-card other-card">
                <div className="card-header">
                  <span className="agent-badge">{agentName(otherTrace.agent)}</span>
                  <span className="meta-time">
                    {dateTime(otherTrace.started_at)} · {seconds(otherTrace.duration_ms)}
                  </span>
                </div>
                <div className="compare-content text">{getAnswer(otherTrace)}</div>
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <p className="hint empty-compare">
          В журнале пока нет других запросов с идентичным текстом вопроса. Задайте такой же вопрос другому агенту (например, Консультанту) или повторите прогон после настройки параметров.
        </p>
      )}
    </section>
  );
});
