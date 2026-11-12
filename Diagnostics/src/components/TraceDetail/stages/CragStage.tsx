import { memo } from 'react';
import { type TraceEvent } from '../../../types';

export const CragStage = memo(function CragStage({ event }: { event: TraceEvent }) {
  if (event.stage === 'crag_retrieve') {
    return (
      <div className="crag-stage-card">
        <p><strong>Извлечение документов (CRAG):</strong> {event.query}</p>
        <p className="hint">
          Режим: {event.mode || 'векторный'} · Объём: {event.chars ?? event.length ?? 0} симв.
        </p>
      </div>
    );
  }

  if (event.stage === 'crag_grade_documents') {
    const isRelevant = event.relevant === true;
    return (
      <div className="crag-stage-card">
        <div className="status-banner">
          <span className={`status-pill ${isRelevant ? 'good-pill' : 'bad-pill'}`}>
            {isRelevant ? '✓ Документы релевантны вопросу' : '✗ Документы признаны нерелевантными'}
          </span>
          {event.fast_path ? <span className="badge-highlight">быстрый путь</span> : null}
        </div>
        {event.query ? <p className="hint">Проверяемый запрос: {event.query}</p> : null}
      </div>
    );
  }

  if (event.stage === 'crag_rewrite_query') {
    return (
      <div className="crag-stage-card">
        <p className="bad">
          <strong>Переформулирование поискового запроса (попытка #{event.attempt})</strong>
        </p>
        <dl className="route-data">
          <dt>Исходный запрос:</dt>
          <dd>{event.old_query}</dd>
          <dt>Переформулированный:</dt>
          <dd><strong>{event.new_query}</strong></dd>
        </dl>
      </div>
    );
  }

  if (event.stage === 'crag_check_groundedness') {
    const isGrounded = event.grounded === true;
    return (
      <div className="crag-stage-card">
        <div className="status-banner">
          <span className={`status-pill ${isGrounded ? 'good-pill' : 'bad-pill'}`}>
            {isGrounded
              ? '✓ Ответ полностью подтверждён источниками (Groundedness OK)'
              : '✗ Обнаружена галлюцинация или нехватка подтверждающих фактов'}
          </span>
        </div>
      </div>
    );
  }

  if (event.stage === 'crag_fallback') {
    return (
      <div className="crag-stage-card bad-border">
        <p className="bad">
          <strong>Переход к резервному ответу</strong> после {event.attempts} попыток переформулирования запроса.
        </p>
      </div>
    );
  }

  return null;
});
