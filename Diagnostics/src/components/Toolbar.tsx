import { memo } from 'react';
import { RefreshCw } from 'lucide-react';
import { diagnosticTabs, matchesTab, type DiagnosticTab, type TraceSummary } from '../types';

interface ToolbarProps {
  tab: DiagnosticTab;
  onTabChange: (tab: DiagnosticTab) => void;
  items: TraceSummary[];
  loading: boolean;
  autoRefresh: boolean;
  onAutoRefreshChange: (enabled: boolean) => void;
  onRefresh: () => void;
}

export const Toolbar = memo(function Toolbar({
  tab,
  onTabChange,
  items,
  loading,
  autoRefresh,
  onAutoRefreshChange,
  onRefresh,
}: ToolbarProps) {
  return (
    <div className="diagnostic-toolbar">
      <nav className="diagnostic-tabs" aria-label="Раздел диагностики">
        {diagnosticTabs.map(item => {
          const count = items.filter(trace => matchesTab(trace, item.id)).length;
          const isCurrent = tab === item.id;
          return (
            <button
              key={item.id}
              className={`tab-button ${isCurrent ? 'active' : ''}`}
              aria-current={isCurrent ? 'page' : undefined}
              onClick={() => onTabChange(item.id)}
            >
              {item.label}
              <span className="tab-count">{count}</span>
            </button>
          );
        })}
      </nav>

      <div className="refresh-group">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={e => onAutoRefreshChange(e.target.checked)}
          />
          <span>Автообновление (10 с)</span>
        </label>
        <button
          className="refresh-button"
          onClick={onRefresh}
          disabled={loading}
          aria-label="Обновить журнал"
        >
          <RefreshCw size={15} className={loading ? 'spin' : ''} aria-hidden="true" />
          <span>Обновить</span>
        </button>
      </div>
    </div>
  );
});
