import { memo } from 'react';
import { ArrowDown, Activity, SearchX } from 'lucide-react';
import { type TraceSummary } from '../types';
import { TraceCard } from './TraceCard';

interface TraceListProps {
  items: TraceSummary[];
  loading: boolean;
  selectedId: string;
  onSelect: (id: string) => void;
  visibleCount: number;
  onLoadMore: () => void;
  hasTotalItems: boolean;
  onResetFilters?: () => void;
}

export const TraceList = memo(function TraceList({
  items,
  loading,
  selectedId,
  onSelect,
  visibleCount,
  onLoadMore,
  hasTotalItems,
  onResetFilters,
}: TraceListProps) {
  const visibleItems = items.slice(0, visibleCount);
  const hasMore = items.length > visibleCount;

  return (
    <section className="journal-panel" aria-label="Журнал запросов" aria-busy={loading}>
      <div className="journal-header">
        <h2>
          Запросы <span className="count-badge">{items.length}</span>
        </h2>
        <span className="sort-hint">
          <ArrowDown size={12} aria-hidden="true" />
          Новые сверху
        </span>
      </div>

      <div className="trace-scroll-area">
        {loading && !items.length ? (
          <div className="empty-state" role="status">
            <Activity size={24} className="spin" aria-hidden="true" />
            <p>Загружаем журнал обращений…</p>
          </div>
        ) : items.length > 0 ? (
          <div className="trace-cards-container">
            {visibleItems.map(item => (
              <TraceCard
                key={item.id}
                item={item}
                selected={selectedId === item.id}
                onSelect={onSelect}
              />
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <SearchX size={32} className="muted" aria-hidden="true" />
            <h3>{hasTotalItems ? 'Ничего не найдено' : 'Пока нет запросов'}</h3>
            <p>
              {hasTotalItems
                ? 'Попробуйте изменить поисковый запрос или фильтры.'
                : 'Отправьте запрос ассистенту или консультанту. Завершённый запрос появится здесь.'}
            </p>
            {hasTotalItems && onResetFilters ? (
              <button type="button" className="reset-inline-btn" onClick={onResetFilters}>
                Сбросить фильтры
              </button>
            ) : null}
          </div>
        )}

        {hasMore ? (
          <div className="load-more-container">
            <button
              type="button"
              className="load-more-btn"
              onClick={onLoadMore}
            >
              Показать ещё (+40 из {items.length - visibleCount})
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
});
