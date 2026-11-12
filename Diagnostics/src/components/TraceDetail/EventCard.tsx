import { memo } from 'react';
import { ChevronDown, AlertCircle, ShieldAlert, CheckCircle2 } from 'lucide-react';
import { type TraceEvent, text } from '../../types';
import { eventNames, seconds } from '../../utils/formatters';
import { LazyDetails } from '../../LazyDetails';

import { RetrievalStage } from './stages/RetrievalStage';
import { DatabaseStage } from './stages/DatabaseStage';
import { CodingStage } from './stages/CodingStage';
import { CragStage } from './stages/CragStage';
import { ModelStage } from './stages/ModelStage';
import { OcrStage } from './stages/OcrStage';
import { AnswerStage } from './AnswerStage';

interface EventCardProps {
  event: TraceEvent;
  index: number;
  traceId: string;
}

const EventContent = memo(function EventContent({ event }: { event: TraceEvent }) {
  const stage = event.stage;

  if (stage.startsWith('ocr_')) {
    return <OcrStage event={event} />;
  }

  if (stage.startsWith('db_')) {
    return <DatabaseStage event={event} />;
  }

  if (stage.startsWith('coding_')) {
    return <CodingStage event={event} />;
  }

  if (stage.startsWith('crag_')) {
    return <CragStage event={event} />;
  }

  if (stage === 'context' || stage === 'generation') {
    return <ModelStage event={event} />;
  }

  if (stage === 'retrieval' || stage === 'search_request') {
    return <RetrievalStage event={event} />;
  }

  if (stage === 'answer') {
    return <AnswerStage event={event} />;
  }

  if (stage === 'route') {
    return (
      <dl className="route-data">
        <dt>Режим обработки:</dt>
        <dd>
          <strong>{event.mode || 'Стандартный'}</strong>
          {event.branch ? ` (ветка: ${event.branch})` : ''}
        </dd>
        <dt>Доступные инструменты:</dt>
        <dd>{event.tools?.length ? event.tools.join(', ') : 'Без внешних инструментов'}</dd>
        {event.model_first_tools !== undefined ? (
          <>
            <dt>Выбор инструмента:</dt>
            <dd>{event.model_first_tools ? 'Сначала модель (Model-first)' : 'По правилам приложения'}</dd>
          </>
        ) : null}
      </dl>
    );
  }

  if (stage === 'specialist_dispatch') {
    return (
      <div className="specialist-box">
        <p>
          <strong>Целевая ветка / специалист:</strong>{' '}
          <span className="badge-highlight">{event.name || event.specialist}</span>
        </p>
        {event.question ? <p className="hint">Входящий запрос ветки: {event.question}</p> : null}
        {event.result ? <p className="good"><strong>Результат:</strong> {event.result}</p> : null}
      </div>
    );
  }

  if (stage === 'supervisor_review') {
    return (
      <div className="supervisor-box">
        <p><strong>Итоговая верификация ответа супервизором.</strong></p>
        {event.active ? <p className="hint">Активный специалист: {event.active}</p> : null}
        {event.sources?.length ? (
          <p className="hint">
            Подтверждающие источники: {event.sources.map(s => s.title).join(', ')}
          </p>
        ) : null}
      </div>
    );
  }

  if (stage === 'guard') {
    const isBlocked = event.status === 'blocked';
    return (
      <div className={`guard-status-box ${isBlocked ? 'blocked' : 'passed'}`}>
        {isBlocked ? <ShieldAlert size={16} className="bad" /> : <CheckCircle2 size={16} className="good" />}
        <span>
          <strong>{event.name || 'Проверка безопасности'}:</strong>{' '}
          {isBlocked ? 'Ответ заблокирован guard-политикой' : 'Проверка пройдена успешно'}
        </span>
      </div>
    );
  }

  if (stage === 'error') {
    return (
      <div className="error-event-box">
        <AlertCircle size={16} className="bad" />
        <div>
          <strong>{event.detail || 'Сбой выполнения'}:</strong>
          <p className="bad-text">{event.error || 'Неизвестная ошибка'}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="generic-event-box">
      {event.detail ? <p>{event.detail}</p> : null}
      {event.content !== undefined ? <pre className="code-block">{text(event.content)}</pre> : null}
    </div>
  );
});

export const EventCard = memo(function EventCard({ event, index, traceId }: EventCardProps) {
  const isError = event.status === 'error' || event.stage === 'error' || Boolean(event.error);
  const isBlocked = event.status === 'blocked';
  const stageTitle =
    event.scope === 'publication_gateway'
      ? 'Поиск опубликованных материалов'
      : eventNames[event.stage] || event.stage;

  return (
    <LazyDetails
      className={`event-card ${isError ? 'has-error' : isBlocked ? 'is-blocked' : ''}`}
      id={`${traceId}-${index}`}
      summary={
        <div className="event-summary-row">
          <div className="summary-left">
            <span className="event-step-number">{index + 1}</span>
            <span className="event-stage-name">{stageTitle}</span>
            {isBlocked ? (
              <span className="status-tag bad">блокировка</span>
            ) : isError ? (
              <span className="status-tag bad">ошибка</span>
            ) : null}
          </div>

          <div className="summary-right">
            <span className="event-elapsed-time">+{seconds(event.elapsed_ms)}</span>
            {event.duration_ms !== undefined ? (
              <span className="event-duration-time">({seconds(event.duration_ms)})</span>
            ) : null}
            <ChevronDown size={14} className="expand-indicator" aria-hidden="true" />
          </div>
        </div>
      }
    >
      {() => (
        <div className="event-card-body">
          <EventContent event={event} />
          <LazyDetails className="raw-event-details" summary="Все параметры события для анализа (JSON)">
            {() => (
              <pre className="code-block raw-json-block" style={{ marginTop: '10px', maxHeight: '350px', overflow: 'auto', fontSize: '12px' }}>
                {JSON.stringify(event, null, 2)}
              </pre>
            )}
          </LazyDetails>
        </div>
      )}
    </LazyDetails>
  );
});
