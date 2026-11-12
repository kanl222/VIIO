import { memo, type ChangeEvent } from 'react';
import { Search, X, RotateCcw } from 'lucide-react';
import { stageNames, type DiagnosticTab } from '../types';

interface FiltersProps {
  tab: DiagnosticTab;
  query: string;
  onQueryChange: (query: string) => void;
  agent: string;
  onAgentChange: (agent: string) => void;
  status: string;
  onStatusChange: (status: string) => void;
  stage: string;
  onStageChange: (stage: string) => void;
  onReset: () => void;
  hasActiveFilters: boolean;
}

export const Filters = memo(function Filters({
  tab,
  query,
  onQueryChange,
  agent,
  onAgentChange,
  status,
  onStatusChange,
  stage,
  onStageChange,
  onReset,
  hasActiveFilters,
}: FiltersProps) {
  return (
    <form className="filters-container" onSubmit={e => e.preventDefault()}>
      <div className="search-field">
        <label htmlFor="search-input" className="field-label">Текст запроса или ID</label>
        <div className="search-input-wrapper">
          <Search size={16} className="search-icon" aria-hidden="true" />
          <input
            id="search-input"
            type="search"
            placeholder="Введите текст или ID…"
            value={query}
            onChange={(e: ChangeEvent<HTMLInputElement>) => onQueryChange(e.target.value)}
          />
          {query ? (
            <button
              type="button"
              className="clear-search-btn"
              onClick={() => onQueryChange('')}
              aria-label="Очистить поиск"
            >
              <X size={14} aria-hidden="true" />
            </button>
          ) : null}
        </div>
      </div>

      {tab === 'agents' ? (
        <div className="select-field">
          <label htmlFor="agent-select" className="field-label">Агент</label>
          <select
            id="agent-select"
            value={agent}
            onChange={(e: ChangeEvent<HTMLSelectElement>) => onAgentChange(e.target.value)}
          >
            <option value="">Все агенты</option>
            <option value="main">Основной</option>
            <option value="consultant">Консультант</option>
          </select>
        </div>
      ) : null}

      <div className="select-field">
        <label htmlFor="status-select" className="field-label">Результат</label>
        <select
          id="status-select"
          value={status}
          onChange={(e: ChangeEvent<HTMLSelectElement>) => onStatusChange(e.target.value)}
        >
          <option value="">Все результаты</option>
          <option value="error">Только ошибки</option>
          <option value="ok">Только успешные</option>
        </select>
      </div>

      {tab === 'agents' ? (
        <div className="select-field">
          <label htmlFor="stage-select" className="field-label">Этап разбора</label>
          <select
            id="stage-select"
            value={stage}
            onChange={(e: ChangeEvent<HTMLSelectElement>) => onStageChange(e.target.value)}
          >
            <option value="">Все запросы</option>
            <option value="unreviewed">Без оценки</option>
            {Object.entries(stageNames).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {hasActiveFilters ? (
        <div className="reset-field">
          <button
            type="button"
            className="reset-filters-btn"
            onClick={onReset}
            title="Сбросить все фильтры"
          >
            <RotateCcw size={14} aria-hidden="true" />
            <span>Сбросить</span>
          </button>
        </div>
      ) : null}
    </form>
  );
});
