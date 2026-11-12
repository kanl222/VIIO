import { memo, useMemo } from 'react';
import { Download, AlertTriangle, Cpu, Terminal, ArrowRight } from 'lucide-react';
import { type DiagnosticTab, type Review, type Trace, type TraceSummary } from '../../types';
import { agentName, dateTime, seconds, formatTokens } from '../../utils/formatters';
import { LazyDetails } from '../../LazyDetails';

import { TraceGraph } from './TraceGraph';
import { QuickNav } from './QuickNav';
import { EventCard } from './EventCard';
import { AnswerStage } from './AnswerStage';
import { ReviewPanel } from './ReviewPanel';
import { ComparePanel } from './ComparePanel';

interface TraceDetailViewProps {
  trace: Trace;
  items: TraceSummary[];
  onSaved: (id: string, review: Review) => void;
  tab?: DiagnosticTab;
}

export const TraceDetailView = memo(function TraceDetailView({
  trace,
  items,
  onSaved,
  tab = 'agents',
}: TraceDetailViewProps) {
  const exportTrace = () => {
    const jsonStr = JSON.stringify(trace, null, 2);
    const blob = new Blob([jsonStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `viio-trace-${trace.id}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const jumpToEvent = (index: number) => {
    const target = document.getElementById(`${trace.id}-${index}`) as HTMLDetailsElement | null;
    if (target) {
      target.open = true;
      target.scrollIntoView({ block: 'start', behavior: 'smooth' });
      target.querySelector('summary')?.focus();
    }
  };

  const jumpToStage = (stagePrefix: string) => {
    const idx = trace.events.findIndex(
      e => e.stage === stagePrefix || e.stage.startsWith(stagePrefix)
    );
    if (idx !== -1) {
      jumpToEvent(idx);
    }
  };

  const totalTokens = useMemo(() => {
    return trace.events.reduce((sum, e) => {
      const u = e.usage;
      const t =
        u?.total_tokens ??
        ((u?.input_tokens ?? u?.prompt_tokens ?? 0) +
          (u?.output_tokens ?? u?.completion_tokens ?? 0));
      return sum + (t || 0);
    }, 0);
  }, [trace.events]);

  const answerEvent = useMemo(
    () => [...trace.events].reverse().find(event => event.stage === 'answer'),
    [trace.events]
  );

  // If OCR or standalone Search tab
  if (tab !== 'agents') {
    const stages =
      tab === 'ocr'
        ? ['ocr_request', 'ocr_result', 'capture_limit']
        : ['search_request', 'retrieval', 'capture_limit'];
    const filteredEvents = trace.events.filter(event => stages.includes(event.stage));

    return (
      <div className="trace-detail-view-container standalone-mode">
        <header className="detail-header-card">
          <div className="title-row">
            <h2>{tab === 'ocr' ? 'Распознавание PDF документа' : trace.question}</h2>
            <button
              type="button"
              className="export-json-btn"
              onClick={exportTrace}
              title="Скачать JSON трассы"
            >
              <Download size={14} aria-hidden="true" />
              <span>JSON</span>
            </button>
          </div>

          <div className="metadata-pills">
            <span className="meta-pill agent-pill">{agentName(trace.agent)}</span>
            <span className="meta-pill time-pill">{dateTime(trace.started_at)}</span>
            <span className="meta-pill duration-pill">{seconds(trace.duration_ms)}</span>
            <span className="meta-pill id-pill">ID: {trace.id}</span>
            {trace.thread_id ? (
              <span className="meta-pill thread-pill">Задача: {trace.thread_id}</span>
            ) : null}
          </div>
        </header>

        {trace.status === 'error' ? (
          <div className="detail-error-banner" role="alert">
            <AlertTriangle size={18} />
            <div>
              <strong>Обработка завершилась ошибкой:</strong>
              <p>{trace.error || 'Неизвестный сбой в процессе выполнения'}</p>
            </div>
          </div>
        ) : null}

        <div className="events-stream-section">
          {filteredEvents.length > 0 ? (
            filteredEvents.map((event, index) => (
              <EventCard key={index} event={event} index={index} traceId={trace.id} />
            ))
          ) : (
            <p className="hint empty-events-hint">Данные этого этапа в журнале отсутствуют.</p>
          )}
        </div>
      </div>
    );
  }

  // Full Agent trace view
  return (
    <div className="trace-detail-view-container agent-mode">
      <header className="detail-header-card">
        <div className="title-row">
          <h2 className="trace-question-heading">{trace.question}</h2>
          <button
            type="button"
            className="export-json-btn"
            onClick={exportTrace}
            title="Экспорт полной трассы в формате JSON"
          >
            <Download size={14} aria-hidden="true" />
            <span>Экспорт JSON</span>
          </button>
        </div>

        <div className="metadata-pills">
          <span className="meta-pill agent-pill">{agentName(trace.agent)}</span>
          <span className="meta-pill time-pill">{dateTime(trace.started_at)}</span>
          <span className="meta-pill duration-pill">{seconds(trace.duration_ms)}</span>
          {totalTokens > 0 ? (
            <span className="meta-pill tokens-pill">
              <Cpu size={12} aria-hidden="true" />
              {formatTokens(totalTokens)} токенов
            </span>
          ) : null}
          <span className="meta-pill id-pill">Трасса: {trace.id}</span>
          {trace.thread_id ? (
            <span className="meta-pill thread-pill">Диалог: {trace.thread_id}</span>
          ) : null}
        </div>
      </header>

      {trace.configuration && Object.keys(trace.configuration).length > 0 ? (
        <LazyDetails
          className="config-disclosure-card"
          summary={
            <div className="config-summary">
              <Terminal size={14} />
              <span>Параметры запуска и конфигурация графа</span>
            </div>
          }
        >
          {() => <pre className="code-block">{JSON.stringify(trace.configuration, null, 2)}</pre>}
        </LazyDetails>
      ) : null}

      {trace.status === 'error' ? (
        <div className="detail-error-banner" role="alert">
          <AlertTriangle size={18} />
          <div>
            <strong>Запрос завершился ошибкой:</strong>
            <p>{trace.error || 'Тип ошибки не записан'}</p>
            <span className="hint">Сохранены этапы, выполненные до возникновения сбоя.</span>
          </div>
        </div>
      ) : null}

      {/* DAG Timeline Graph */}
      <TraceGraph events={trace.events} onSelectStep={jumpToEvent} />

      {/* Pipeline Stage Quick Navigation */}
      <QuickNav events={trace.events} onJumpToStage={jumpToStage} />

      {/* Event Timeline Stream */}
      <section className="events-stream-section" aria-label="Детальная лента событий">
        <div className="events-stream-header">
          <h3>Лента событий ({trace.events.length})</h3>
          <span className="hint">Раскройте блок события для инспекции полезной нагрузки</span>
        </div>

        <div className="events-list">
          {trace.events.map((event, index) => (
            <EventCard key={index} event={event} index={index} traceId={trace.id} />
          ))}
        </div>
      </section>

      {/* Final Answer Section */}
      <AnswerStage event={answerEvent} />

      {/* Review & Benchmark Evaluation */}
      <ReviewPanel trace={trace} onSaved={onSaved} />

      {/* Compare Panel with peers */}
      <ComparePanel trace={trace} items={items} />

      <footer className="trace-footer-note">
        <ArrowRight size={14} aria-hidden="true" />
        <span>
          Причина ошибки определяется анализом данных трассы и сравнением с эталоном. Журнал не назначает её автоматически.
        </span>
      </footer>
    </div>
  );
});
