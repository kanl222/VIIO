import { memo } from 'react';
import { type TraceEvent } from '../../../types';
import { LazyDetails } from '../../../LazyDetails';

export const CragStage = memo(function CragStage({ event }: { event: TraceEvent }) {
  if (event.stage === 'crag_retrieve') {
    return (
      <div className="crag-stage-card">
        <p><strong>Извлечение документов (CRAG):</strong> {event.query}</p>
        <p className="hint">
          Режим: {event.mode || 'векторный'} · Отобрано: {event.chars ?? event.length ?? 0} симв.
          {event.raw_chars !== undefined ? ` (сырых: ${event.raw_chars} симв.)` : ''}
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
          {event.method ? <span className="badge-highlight">метод: {event.method}</span> : null}
        </div>

        {event.filtered_chunks && event.filtered_chunks.length > 0 ? (
          <div className="filter-reranker-box" style={{ marginTop: '8px', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <span className="badge-highlight good">
              Filter-Reranker: отобрано [{event.filtered_chunks.join(', ')}] из {event.total_chunks ?? event.filtered_chunks.length} фрагментов
            </span>
            {event.noise_removed !== undefined && event.noise_removed > 0 ? (
              <span className="badge-highlight">Отсечено шума: {event.noise_removed} шт.</span>
            ) : null}
          </div>
        ) : null}

        {event.eval_output ? (
          <div className="eval-output-box" style={{ marginTop: '8px', fontSize: '13px' }}>
            <strong>Вердикт модели:</strong> <code>{event.eval_output}</code>
          </div>
        ) : null}

        {event.query ? <p className="hint" style={{ marginTop: '6px' }}>Проверяемый запрос: {event.query}</p> : null}
      </div>
    );
  }

  if (event.stage === 'crag_rewrite_query') {
    return (
      <div className="crag-stage-card">
        <p className="bad">
          <strong>Переформулирование поискового запроса (попытка #{event.attempt})</strong>
          {event.strategy ? <span className="badge-highlight" style={{ marginLeft: '8px' }}>{event.strategy}</span> : null}
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

  if (event.stage === 'crag_generate') {
    return (
      <div className="crag-stage-card">
        <p><strong>Формирование чернового ответа (CRAG Generate)</strong></p>
        <p className="hint">
          Объём ответа: {event.length ?? 0} симв.
          {event.context_chars !== undefined ? ` · Контекст: ${event.context_chars} симв.` : ''}
        </p>
        {event.draft_snippet ? (
          <LazyDetails summary="Фрагмент сформированного ответа">
            {() => <div className="text" style={{ fontSize: '13px', whiteSpace: 'pre-wrap' }}>{event.draft_snippet}</div>}
          </LazyDetails>
        ) : null}
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
          {event.faithfulness_score !== undefined ? (
            <span className={`badge-highlight ${isGrounded ? 'good' : 'bad'}`}>
              Фактологичность: {Math.round(event.faithfulness_score * 100)}%
            </span>
          ) : null}
        </div>

        {event.eval_output ? (
          <div className="eval-output-box" style={{ marginTop: '8px', fontSize: '13px' }}>
            <strong>Вердикт проверки:</strong> <code>{event.eval_output}</code>
          </div>
        ) : null}

        {event.context_chars !== undefined || event.draft_chars !== undefined ? (
          <p className="hint" style={{ marginTop: '6px' }}>
            Сверено: контекст ({event.context_chars ?? 0} симв.) ↔ ответ ({event.draft_chars ?? 0} симв.)
          </p>
        ) : null}
      </div>
    );
  }

  if (event.stage === 'crag_fallback') {
    return (
      <div className="crag-stage-card bad-border">
        <p className="bad">
          <strong>Переход к резервному ответу</strong> после {event.attempts} попыток переформулирования запроса.
        </p>
        {event.question ? <p className="hint">Вопрос: {event.question}</p> : null}
      </div>
    );
  }

  return null;
});
