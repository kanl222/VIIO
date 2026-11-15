import { memo, useMemo, useState } from 'react';
import { Download, AlertTriangle, Cpu, Terminal, ArrowRight, Copy, Check } from 'lucide-react';
import { type DiagnosticTab, type Review, type Trace, type TraceEvent, type TraceSummary } from '../../types';
import { agentName, dateTime, seconds, formatTokens, copyToClipboard } from '../../utils/formatters';
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
  const [copiedQuestion, setCopiedQuestion] = useState(false);
  const [copiedAnswer, setCopiedAnswer] = useState(false);

  const copyQuestion = async () => {
    if (!trace.question) return;
    const ok = await copyToClipboard(trace.question);
    if (ok) {
      setCopiedQuestion(true);
      setTimeout(() => setCopiedQuestion(false), 1500);
    }
  };

  const copyAnswer = async () => {
    const textToCopy = answerEvent?.reply;
    if (!textToCopy) return;
    const ok = await copyToClipboard(textToCopy);
    if (ok) {
      setCopiedAnswer(true);
      setTimeout(() => setCopiedAnswer(false), 1500);
    }
  };

  const jumpToReview = () => {
    const el = document.getElementById('review-section');
    if (el) {
      el.scrollIntoView({ block: 'start', behavior: 'smooth' });
      const firstInput = el.querySelector<HTMLElement>('select, input, textarea');
      firstInput?.focus();
    }
  };

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

  const jumpToMatcher = (matcher: (e: TraceEvent) => boolean) => {
    const idx = trace.events.findIndex(matcher);
    if (idx !== -1) {
      jumpToEvent(idx);
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

  const ragMetrics = useMemo(() => {
    const retrievalEvents = trace.events.filter(e => e.stage === 'retrieval');
    const cragGradeEvents = trace.events.filter(e => e.stage === 'crag_grade_documents');
    const cragGroundedEvents = trace.events.filter(e => e.stage === 'crag_check_groundedness');
    const cragRewriteEvents = trace.events.filter(e => e.stage === 'crag_rewrite_query');

    if (!retrievalEvents.length && !cragGradeEvents.length && !cragGroundedEvents.length && !cragRewriteEvents.length) {
      return null;
    }

    const latestRetrieval = retrievalEvents[retrievalEvents.length - 1];
    const candidateCount = latestRetrieval?.ranking_details?.before_access_check;
    const admittedCount =
      latestRetrieval?.ranking_details?.after_access_check;
    const selectedCount = latestRetrieval?.selected?.length;
    const uShaped = latestRetrieval?.ranking_details?.lost_in_the_middle_reordered;
    const rerankerStatus = latestRetrieval?.reranker_status;
    const reranker = rerankerStatus === 'lexical_fallback' || latestRetrieval?.reranker_used === 'lexical_fallback'
      ? 'Лексический резерв'
      : rerankerStatus === 'disabled' ? 'Реранкер выключен'
      : rerankerStatus === 'skipped' || latestRetrieval?.lookup_mode === 'document_ids' ? 'Переранжирование пропущено'
      : latestRetrieval?.reranker_model || latestRetrieval?.reranker_used || 'Нет данных о реранкере';
    const cacheBackend = latestRetrieval?.cache_backend;
    const fromCache = latestRetrieval?.from_cache;
    const cacheStatus = latestRetrieval?.cache_status;

    const latestGrade = cragGradeEvents[cragGradeEvents.length - 1];
    const isRelevant = latestGrade?.relevant;
    const noiseRemoved =
      latestGrade?.noise_removed ??
      (latestGrade?.total_chunks != null && latestGrade.filtered_chunks
        ? latestGrade.total_chunks - latestGrade.filtered_chunks.length
        : undefined);
    const filterMethod = latestGrade?.method;

    const latestGrounded = cragGroundedEvents[cragGroundedEvents.length - 1];
    const isGrounded = latestGrounded?.grounded;

    const rewriteAttempts = cragRewriteEvents.length;

    return {
      candidateCount,
      admittedCount,
      recordedCount: latestRetrieval?.candidates?.length,
      selectedCount,
      uShaped,
      isRelevant,
      noiseRemoved,
      filterMethod,
      isGrounded,
      rewriteAttempts,
      reranker,
      fromCache,
      cacheBackend,
      cacheStatus,
    };
  }, [trace.events]);

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
          <div className="header-actions">
            <button
              type="button"
              className="copy-header-btn"
              onClick={copyQuestion}
              title="Скопировать вопрос в буфер обмена"
            >
              {copiedQuestion ? <Check size={13} className="good" /> : <Copy size={13} />}
              <span>{copiedQuestion ? 'Скопировано' : 'Копировать вопрос'}</span>
            </button>
            {answerEvent?.reply ? (
              <button
                type="button"
                className="copy-header-btn"
                onClick={copyAnswer}
                title="Скопировать итоговый ответ модели"
              >
                {copiedAnswer ? <Check size={13} className="good" /> : <Copy size={13} />}
                <span>{copiedAnswer ? 'Скопировано' : 'Копировать ответ'}</span>
              </button>
            ) : null}
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

      {trace.payload_truncated ? (
        <div className="payload-truncated-banner" role="alert">
          <AlertTriangle size={18} aria-hidden="true" />
          <div>
            <strong>Трасса превысила 8 МБ (содержимое усечено):</strong>
            <p>Часть сырых данных (большие дампы DDL схемы и фрагменты текста) была отсечена коллектором для предотвращения переполнения диска.</p>
          </div>
        </div>
      ) : null}

      {ragMetrics ? (
        <section className="rag-analysis-dashboard" aria-labelledby="rag-metrics-title">
          <h3 id="rag-metrics-title">Метрики поиска и проверки ответа</h3>
          <dl className="rag-metrics-grid">
            <div className="rag-metric">
              <dt>Кандидаты поиска</dt>
              <dd>
                <strong>
                  {ragMetrics.candidateCount != null || ragMetrics.admittedCount != null
                    ? `${ragMetrics.candidateCount ?? '—'} до проверки доступа · ${ragMetrics.admittedCount ?? '—'} допущено`
                    : ragMetrics.recordedCount != null ? `Записано: ${ragMetrics.recordedCount}` : 'Нет данных'}
                </strong>
                <span>Отобрано фрагментов: {ragMetrics.selectedCount ?? '—'}</span>
              </dd>
            </div>
            <div className="rag-metric">
              <dt>Порядок фрагментов</dt>
              <dd>
                <strong>
                  {ragMetrics.uShaped === true ? 'U-образная раскладка'
                    : ragMetrics.uShaped === false ? 'Стандартный порядок' : 'Нет данных'}
                </strong>
              </dd>
            </div>
            <div className="rag-metric">
              <dt>Релевантность фрагментов</dt>
              <dd>
                <strong data-result={ragMetrics.isRelevant}>
                  {ragMetrics.isRelevant === true ? 'Релевантны'
                    : ragMetrics.isRelevant === false ? 'Нерелевантны' : 'Нет оценки в трассе'}
                </strong>
                {ragMetrics.noiseRemoved != null && <span>Отсечено фрагментов: {ragMetrics.noiseRemoved}</span>}
                {ragMetrics.filterMethod && <span>Метод: {ragMetrics.filterMethod}</span>}
              </dd>
            </div>
            <div className="rag-metric">
              <dt>Подтверждение ответа</dt>
              <dd>
                <strong data-result={ragMetrics.isGrounded}>
                  {ragMetrics.isGrounded === true ? 'Подтверждён источниками'
                    : ragMetrics.isGrounded === false ? 'Не подтверждён источниками' : 'Нет оценки в трассе'}
                </strong>
                {ragMetrics.rewriteAttempts > 0 && <span>Переформулировок запроса: {ragMetrics.rewriteAttempts}</span>}
              </dd>
            </div>
            <div className="rag-metric">
              <dt>Переранжирование и кэш</dt>
              <dd>
                <strong>{ragMetrics.reranker}</strong>
                <span>{ragMetrics.cacheStatus === 'hit' || ragMetrics.fromCache
                  ? `Кандидаты из кэша${ragMetrics.cacheBackend ? ` (${ragMetrics.cacheBackend})` : ''}`
                  : ragMetrics.cacheStatus === 'miss' ? 'Кэш кандидатов: промах'
                  : ragMetrics.cacheStatus === 'disabled' ? 'Кэш выключен'
                  : ragMetrics.cacheStatus === 'skipped' ? 'Кэш пропущен'
                  : 'Нет данных о кэше'}</span>
              </dd>
            </div>
          </dl>
        </section>
      ) : null}

      <TraceGraph events={trace.events} onSelectStep={jumpToEvent} />

      {/* Pipeline Stage Quick Navigation */}
      <QuickNav
        events={trace.events}
        onJumpToMatcher={jumpToMatcher}
        onJumpToReview={jumpToReview}
        hasReview={Boolean(trace.review?.stage || trace.review?.score != null || trace.review?.notes)}
      />

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
