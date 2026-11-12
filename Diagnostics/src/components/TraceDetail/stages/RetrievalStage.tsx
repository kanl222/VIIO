import { memo } from 'react';
import { type Fragment, type TraceEvent } from '../../../types';
import { scoreLabel, searchBranchNames } from '../../../utils/formatters';
import { LazyDetails } from '../../../LazyDetails';

interface FragmentsProps {
  items: Fragment[] | undefined;
  label: string;
}

const Fragments = memo(function Fragments({ items, label }: FragmentsProps) {
  const displayed = items?.some(item => item.score !== undefined)
    ? [...items].sort((a, b) => (a.score_rank ?? Infinity) - (b.score_rank ?? Infinity))
    : items;

  return (
    <section className="fragments-column">
      <h4 className="column-title">
        {label} {items ? <span className="muted-count">({items.length})</span> : null}
      </h4>
      <div className="fragment-list">
        {displayed === undefined ? (
          <p className="hint">Этот этап не записан в трассе.</p>
        ) : displayed.length === 0 ? (
          <p className="hint">Ничего не найдено.</p>
        ) : (
          displayed.map((item, index) => (
            <LazyDetails
              className="fragment-card"
              key={`${item.document_id || 'doc'}-${index}`}
              summary={
                <div className="fragment-summary">
                  <span className="rank-badge">
                    {item.score === undefined ? index + 1 : item.score_rank ?? '—'}
                  </span>
                  <span className="fragment-title">{item.title || 'Без названия'}</span>
                  {item.context_position !== undefined ? (
                    <span className="badge-highlight" style={{ fontSize: '11px', padding: '1px 5px' }}>
                      #{item.context_position} в контексте
                    </span>
                  ) : null}
                  {item.cross_encoder_score !== undefined ? (
                    <span className="badge-highlight good" style={{ fontSize: '11px', padding: '1px 5px' }} title="Cross-Encoder Relevance Logit">
                      ONNX: {item.cross_encoder_score.toFixed(2)}
                    </span>
                  ) : null}
                  {item.selection_reason ? (
                    <span className="badge-highlight" style={{ fontSize: '11px', padding: '1px 5px' }}>
                      {item.selection_reason === 'procedural_neighbor' ? 'сосед' : 'ранжирован'}
                    </span>
                  ) : null}
                  {item.score !== undefined ? (
                    <span className="fragment-score">
                      {scoreLabel.format(item.score)} б.
                    </span>
                  ) : null}
                </div>
              }
            >
              {() => (
                <div className="fragment-details">
                  <div className="fragment-meta">
                    <span>ID: {item.document_id || 'не записан'}</span>
                    {item.version ? <span>версия {item.version}</span> : null}
                    {item.chunk_id ? <span>чанк {item.chunk_id}</span> : null}
                    {item.line_start ? <span>строка {item.line_start}</span> : null}
                    {item.filename ? <span>файл: {item.filename}</span> : null}
                  </div>

                  {item.score !== undefined ? (
                    <dl className="score-breakdown">
                      {item.cross_encoder_score !== undefined ? (
                        <>
                          <dt>Cross-Encoder (ONNX):</dt>
                          <dd>
                            <strong style={{ color: 'var(--color-success, #16a34a)' }}>{item.cross_encoder_score.toFixed(4)}</strong>
                            {' '}(модель: {item.reranker_model || 'bge-reranker-v2-m3'}, ранг: {item.reranker_rank ?? '—'})
                          </dd>
                        </>
                      ) : null}
                      <dt>Итоговый балл:</dt>
                      <dd>
                        <strong>{scoreLabel.format(item.score)}</strong> = {item.term_score ?? 0} (слова) + {scoreLabel.format(item.rrf_score ?? 0)} (RRF ранг)
                      </dd>
                      <dt>Совпадения:</dt>
                      <dd>
                        Текст: {item.matched_text?.join(', ') || '—'} · Заголовок: {item.matched_title?.join(', ') || '—'} · Файл: {item.matched_filename?.join(', ') || '—'}
                      </dd>
                      <dt>Позиции по веткам:</dt>
                      <dd>
                        {Object.entries(item.branch_ranks ?? {})
                          .map(([name, ranks]) => `${searchBranchNames[name] || name}: [${ranks.join(', ')}]`)
                          .join(' · ') || '—'}
                      </dd>
                      <dt>Отбор:</dt>
                      <dd>
                        По баллу: {item.score_rank ?? 'ниже порога'} · Разнообразие: {item.diversity_rank ?? '—'} · В выдаче:{' '}
                        <strong>{item.final_rank ?? 'нет'}</strong>
                        {item.deferred_for_diversity ? ' (смещён из-за более высокого чанка того же документа)' : ''}
                      </dd>
                    </dl>
                  ) : null}

                  {item.selection_reason === 'procedural_neighbor' ? (
                    <p className="hint neighbor-hint">Добавлен как смежный процедурный фрагмент раздела.</p>
                  ) : null}

                  {item.text !== undefined ? (
                    <div className="text fragment-text-content">{item.text}</div>
                  ) : (
                    <p className="hint">Текст доступен в отобранных фрагментах.</p>
                  )}
                </div>
              )}
            </LazyDetails>
          ))
        )}
      </div>
    </section>
  );
});

export const RetrievalStage = memo(function RetrievalStage({ event }: { event: TraceEvent }) {
  const isError = event.status === 'error';

  return (
    <div className="stage-retrieval">
      <div className="retrieval-query">
        <strong>Поисковый запрос:</strong> <span className="query-text">{event.query}</span>
        {event.from_cache ? (
          <span className="badge-highlight good" style={{ marginLeft: 8 }}>
            ⚡ Из кэша {event.cache_backend === 'redis' ? 'Redis' : 'памяти'}
          </span>
        ) : null}
        {event.reranker_model || event.reranker_used ? (
          <span className="badge-highlight" style={{ marginLeft: 8 }}>
            🎯 Реранкер: {event.reranker_model || event.reranker_used}
          </span>
        ) : null}
      </div>

      {isError ? (
        <div className="bad error-notice">Поиск завершился с ошибкой: {event.error || 'неизвестная ошибка'}</div>
      ) : null}

      {event.branches ? (
        <div className="branch-pills">
          {Object.entries(event.branches).map(([name, value]) => (
            <div
              key={name}
              className={`branch-pill ${value.status === 'error' ? 'bad-pill' : 'ok-pill'}`}
            >
              <span className="branch-name">{searchBranchNames[name] || name}:</span>
              <span className="branch-count">
                {value.status === 'error' ? (value.error || 'ошибка') : `${value.count ?? 0} кандидатов`}
              </span>
              {value.duration_ms !== undefined ? (
                <span className="branch-time">{value.duration_ms} мс</span>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      {event.ranking_details ? (
        <dl className="route-data ranking-params">
          <dt>Ключевые термины:</dt>
          <dd>{event.ranking_details.query_terms.join(', ') || '—'}</dd>
          <dt>Весовые коэффициенты:</dt>
          <dd>
            текст: {event.ranking_details.text_weight} · заголовок/файл: {event.ranking_details.title_or_filename_weight} · порог: {event.ranking_details.min_score} · RRF k={event.ranking_details.rrf_k}
          </dd>
          <dt>Фильтрация прав доступа:</dt>
          <dd>
            до проверки: <strong>{event.ranking_details.before_access_check}</strong> → допущено пользователю: <strong>{event.ranking_details.after_access_check}</strong>
          </dd>
          {event.ranking_details.lost_in_the_middle_reordered ? (
            <>
              <dt>Оптимизация внимания:</dt>
              <dd>
                <span className="badge-highlight good">
                  ✓ U-образная раскладка Lost-in-the-Middle ({event.ranking_details.reordered_count ?? 0} чанков)
                </span>
              </dd>
            </>
          ) : null}
        </dl>
      ) : null}

      {event.lookup_mode === 'document_ids' ? (
        <p className="hint">Поиск внутри конкретных документов (запрошено: {event.requested_documents ?? 'все'}).</p>
      ) : null}

      {event.search_trace_id ? (
        <p className="fragment-meta">
          Трасса RAG: <code>{event.search_trace_id}</code>
        </p>
      ) : null}

      <div className="retrieval-columns-grid">
        <Fragments items={event.candidates} label="Кандидаты поиска" />
        <Fragments items={event.selected} label="Отобранные фрагменты" />
      </div>

      {event.context !== undefined ? (
        <LazyDetails summary="Контекст, переданный в модель">
          {() => <div className="text context-text">{event.context}</div>}
        </LazyDetails>
      ) : null}
    </div>
  );
});
