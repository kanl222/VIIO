import { memo, useState } from 'react';
import { RefreshCw, Download, Check } from 'lucide-react';
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
  const [exporting, setExporting] = useState(false);

  const exportGoldenSet = async () => {
    setExporting(true);
    try {
      const res = await fetch('/api/reviews/export-golden-set');
      const data = await res.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `viio_golden_set_${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      alert('Не удалось выгрузить эталонную выборку');
    } finally {
      setTimeout(() => setExporting(false), 1200);
    }
  };
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
          <span>Обновление в реальном времени</span>
        </label>
        <button
          className="refresh-button export-golden-btn"
          onClick={exportGoldenSet}
          disabled={exporting}
          title="Выгрузить эталонную выборку (Golden Dataset) с экспертными оценками для бенчмаркинга"
        >
          {exporting ? <Check size={14} className="good" /> : <Download size={14} aria-hidden="true" />}
          <span>{exporting ? 'Выгружено' : 'Golden Set'}</span>
        </button>
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
