import { memo } from 'react';
import { CircleAlert, CircleCheck, Search, Cpu } from 'lucide-react';
import { stageNames, type TraceSummary } from '../types';
import { agentName, dateTime, seconds, formatTokens } from '../utils/formatters';

interface TraceCardProps {
  item: TraceSummary;
  selected: boolean;
  onSelect: (id: string) => void;
}

export const TraceCard = memo(function TraceCard({
  item,
  selected,
  onSelect,
}: TraceCardProps) {
  const isError = item.status === 'error' || item.status === 'cancelled';
  const isAgentTrace = !['ocr', 'search'].includes(item.agent);
  const reviewStage = item.review?.stage;
  const reviewScore = item.review?.score;

  return (
    <button
      className={`trace-card-btn ${selected ? 'selected' : ''}`}
      aria-pressed={selected}
      onClick={() => onSelect(item.id)}
      type="button"
    >
      <span className="card-header-row">
        <span className={`agent-badge agent-${item.agent}`}>
          {agentName(item.agent)}
        </span>
        <time className="timestamp-label" dateTime={item.started_at}>
          {dateTime(item.started_at)}
        </time>
      </span>

      <strong className="card-question-text">{item.question || 'Обращение без названия'}</strong>

      <span className="card-stats-row">
        <span className="stat-badge">
          {seconds(item.duration_ms)}
        </span>

        {isAgentTrace ? (
          <>
            <span className="stat-badge" title="Число поисковых обращений">
              <Search size={11} aria-hidden="true" />
              Поиск {item.searches}
            </span>
            <span className="stat-badge" title="Число вызовов модели">
              <Cpu size={11} aria-hidden="true" />
              Модель {item.model_calls}
            </span>
            {typeof item.total_tokens === 'number' ? (
              <span className="stat-badge" title="Всего токенов">
                {formatTokens(item.total_tokens)} ток.
              </span>
            ) : null}
          </>
        ) : null}

        <span className="stat-badge card-status">
          {item.status === 'running' ? 'Выполняется' : isError ? (
            <CircleAlert size={14} className="bad" aria-label="Ошибка выполнения" />
          ) : (
            <CircleCheck size={14} className="good" aria-label="Выполнен" />
          )}
        </span>
      </span>

      {isAgentTrace ? (
          <span className={`card-review-badge ${reviewStage === 'correct' ? 'review-correct' : ''}`}>
            {reviewStage ? stageNames[reviewStage] || reviewStage : 'Без оценки'}
            {typeof reviewScore === 'number' ? ` · ${reviewScore}/5` : ''}
          </span>
      ) : null}
    </button>
  );
});
