import { memo } from 'react';
import { GitBranch, Clock, AlertTriangle, CheckCircle, Database, Search } from 'lucide-react';
import { type TraceEvent } from '../../types';
import { eventNames, getEventName, seconds } from '../../utils/formatters';

interface TraceGraphProps {
  events: TraceEvent[];
  onSelectStep: (index: number) => void;
}

export const TraceGraph = memo(function TraceGraph({ events, onSelectStep }: TraceGraphProps) {
  if (!events.length) {
    return (
      <section className="trace-graph-card" aria-label="Граф выполнения">
        <p className="hint">В трассе нет записанных этапов выполнения.</p>
      </section>
    );
  }

  return (
    <section className="trace-graph-card" aria-label="Шаги выполнения LangGraph">
      <div className="graph-header">
        <div className="title-with-icon">
          <GitBranch size={16} className="accent-icon" aria-hidden="true" />
          <h3>Шаги выполнения ({events.length})</h3>
        </div>
        <span className="hint-label">Выберите шаг, чтобы открыть детали</span>
      </div>

      <div className="trace-graph-scroller">
        <ol className="trace-graph-timeline">
          {events.map((event, index) => {
            const hasError =
              event.stage === 'error' ||
              event.status === 'error' ||
              Boolean(event.error) ||
              event.valid === false ||
              event.relevant === false ||
              event.grounded === false;
            const isBlocked = event.status === 'blocked';
            const isGood =
              event.valid === true || event.relevant === true || event.grounded === true;

            const nodeStatusClass = isBlocked
              ? 'status-blocked'
              : hasError
              ? 'status-error'
              : isGood
              ? 'status-good'
              : 'status-default';

            return (
              <li key={index} className="trace-graph-item">
                <button
                  type="button"
                  className={`trace-graph-node-btn ${nodeStatusClass}`}
                  onClick={() => onSelectStep(index)}
                  title={`Перейти к шагу ${index + 1}: ${getEventName(event)}`}
                >
                  <div className="node-top-row">
                    <span className="node-index">{index + 1}</span>
                    <strong className="node-title">
                      {getEventName(event)}
                    </strong>
                  </div>

                  <div className="node-meta-row">
                    <span className="node-elapsed">
                      <Clock size={11} aria-hidden="true" />
                      +{seconds(event.elapsed_ms)}
                    </span>
                    {event.duration_ms !== undefined ? (
                      <span className="node-duration">({seconds(event.duration_ms)})</span>
                    ) : null}
                  </div>

                  <div className="node-status-row">
                    {isBlocked ? (
                      <span className="status-badge bad">Блокировка</span>
                    ) : hasError ? (
                      <span className="status-badge bad">
                        <AlertTriangle size={11} /> Сбой
                      </span>
                    ) : isGood ? (
                      <span className="status-badge good">
                        <CheckCircle size={11} /> Успешно
                      </span>
                    ) : null}

                    {event.attempt !== undefined ? (
                      <span className="status-badge retry-pill">Попытка #{event.attempt}</span>
                    ) : null}
                  </div>

                  {event.specialist || event.name ? (
                    <div className="node-context-snippet specialist-snippet">
                      <GitBranch size={10} />
                      <span>{event.name || event.specialist}</span>
                    </div>
                  ) : null}

                  {event.stage === 'retrieval' ? (
                    <div className="node-context-snippet retrieval-snippet">
                      <Search size={10} />
                <span>Кандидаты: {event.candidates?.length ?? '—'} · Отобрано: {event.selected?.length ?? '—'}</span>
                    </div>
                  ) : null}

                  {event.tables?.length ? (
                    <div className="node-context-snippet db-snippet">
                      <Database size={10} />
                      <span>Таблицы: {event.tables.slice(0, 2).join(', ')}{event.tables.length > 2 ? '…' : ''}</span>
                    </div>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
});
