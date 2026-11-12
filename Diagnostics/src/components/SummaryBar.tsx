import { memo, useMemo } from 'react';
import { stageNames, type TraceSummary } from '../types';
import { seconds, formatTokens } from '../utils/formatters';

interface SummaryBarProps {
  items: TraceSummary[];
  selectedStage?: string;
  onSelectStage?: (stage: string) => void;
}

export const SummaryBar = memo(function SummaryBar({
  items,
  selectedStage,
  onSelectStage,
}: SummaryBarProps) {
  const metrics = useMemo(() => {
    const errorCount = items.filter(item => item.status === 'error').length;
    const durations = items.map(item => item.duration_ms).sort((a, b) => a - b);
    const p50 = durations.length ? durations[Math.floor((durations.length - 1) * 0.5)] : null;
    const p95 = durations.length ? durations[Math.ceil(durations.length * 0.95) - 1] : null;

    const reviewed = items.filter(item => item.review?.stage);
    const correct = reviewed.filter(item => item.review?.stage === 'correct').length;

    let totalTokens = 0;
    let tokensCount = 0;
    for (const item of items) {
      if (typeof item.total_tokens === 'number') {
        totalTokens += item.total_tokens;
        tokensCount++;
      }
    }

    const stageBreakdown: Record<string, number> = {};
    for (const [key] of Object.entries(stageNames)) {
      if (key !== 'correct' && key !== 'unknown') {
        stageBreakdown[key] = reviewed.filter(item => item.review?.stage === key).length;
      }
    }

    return {
      errorCount,
      p50,
      p95,
      reviewedCount: reviewed.length,
      correctCount: correct,
      totalTokens: tokensCount > 0 ? totalTokens : null,
      stageBreakdown,
    };
  }, [items]);

  const stages = Object.entries(stageNames).filter(([key]) => !['correct', 'unknown'].includes(key));

  return (
    <section className="summary-bar" aria-label="Сводка по отфильтрованным запросам">
      <div className="summary-item">
        <span className="summary-label">Запросы / ошибки</span>
        <strong className="summary-value">
          {items.length}
          <span className="divider">/</span>
          <span className={metrics.errorCount > 0 ? 'bad' : ''}>{metrics.errorCount}</span>
        </strong>
      </div>

      <div className="summary-item">
        <span className="summary-label">Время p50 / p95</span>
        <strong className="summary-value">
          {metrics.p50 !== null && metrics.p95 !== null
            ? `${seconds(metrics.p50)} / ${seconds(metrics.p95)}`
            : 'Нет данных'}
        </strong>
      </div>

      <div className="summary-item">
        <span className="summary-label">Верных из разобранных</span>
        <strong className="summary-value">
          {metrics.reviewedCount > 0
            ? `${metrics.correctCount} из ${metrics.reviewedCount}`
            : 'Ещё нет оценок'}
        </strong>
      </div>

      {metrics.totalTokens !== null ? (
        <div className="summary-item">
          <span className="summary-label">Использовано токенов</span>
          <strong className="summary-value tokens-value">
            {formatTokens(metrics.totalTokens)}
          </strong>
        </div>
      ) : null}

      <div className="summary-stages-breakdown">
        <span className="stage-breakdown-title">Отмеченные причины</span>
        <div className="stage-chips-list">
          {stages.map(([key, label]) => {
            const count = metrics.stageBreakdown[key] || 0;
            const isFilterActive = selectedStage === key;
            return (
              <button
                key={key}
                type="button"
                className={`stage-chip-btn ${isFilterActive ? 'active' : ''}`}
                aria-pressed={isFilterActive}
                onClick={() => onSelectStage?.(isFilterActive ? '' : key)}
                title={`Фильтр по причине: ${label}`}
              >
                <span>{label}</span>
                <strong className="stage-chip-count">{count}</strong>
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
});
