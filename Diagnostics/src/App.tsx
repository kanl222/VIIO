import { useCallback, useDeferredValue, useEffect, useMemo, useState } from 'react';
import { Activity, RefreshCw } from 'lucide-react';
import { matchesTab, type DiagnosticTab } from './types';
import { useTheme } from './hooks/useTheme';
import { useTraces } from './hooks/useTraces';
import { Header } from './components/Header';
import { Toolbar } from './components/Toolbar';
import { SummaryBar } from './components/SummaryBar';
import { Filters } from './components/Filters';
import { TraceList } from './components/TraceList';
import { TraceDetailView } from './components/TraceDetail/TraceDetailView';

export function App() {
  const { dark, toggleTheme } = useTheme();
  const {
    items,
    loading,
    error,
    refresh,
    autoRefresh,
    setAutoRefresh,
    selectedId,
    selectTrace,
    trace,
    detailLoading,
    detailError,
    reloadCurrentDetail,
    handleReviewSaved,
  } = useTraces();

  const [tab, setTab] = useState<DiagnosticTab>('agents');
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);
  const [agent, setAgent] = useState('');
  const [status, setStatus] = useState('');
  const [stage, setStage] = useState('');
  const [visible, setVisible] = useState(40);

  // Keyboard shortcuts: '/' focuses search, 'Escape' clears search or deselects
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === '/' && document.activeElement?.tagName !== 'INPUT' && document.activeElement?.tagName !== 'TEXTAREA') {
        e.preventDefault();
        const searchInput = document.getElementById('search-input') as HTMLInputElement | null;
        searchInput?.focus();
        searchInput?.select();
      } else if (e.key === 'Escape') {
        if (query) {
          setQuery('');
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [query]);

  // Filter items based on tab, query, agent, status, and review stage
  const filteredItems = useMemo(() => {
    const q = deferredQuery.trim().toLocaleLowerCase('ru');
    return items.filter(item => {
      if (!matchesTab(item, tab)) return false;

      if (q) {
        const textTarget = `${item.question} ${item.id}`.toLocaleLowerCase('ru');
        if (!textTarget.includes(q)) return false;
      }

      if (tab === 'agents' && agent && item.agent !== agent) return false;
      if (status && item.status !== status) return false;

      if (tab === 'agents' && stage) {
        if (stage === 'unreviewed') {
          if (item.review?.stage) return false;
        } else if (item.review?.stage !== stage) {
          return false;
        }
      }

      return true;
    });
  }, [items, deferredQuery, agent, status, stage, tab]);

  // Reset visible count when filters change
  useEffect(() => {
    setVisible(40);
  }, [deferredQuery, agent, status, stage, tab]);

  const handleTabChange = useCallback((nextTab: DiagnosticTab) => {
    setTab(nextTab);
    const current = items.find(item => item.id === selectedId);
    if (current && !matchesTab(current, nextTab)) {
      selectTrace('');
    }
  }, [items, selectedId, selectTrace]);

  const handleResetFilters = useCallback(() => {
    setQuery('');
    setAgent('');
    setStatus('');
    setStage('');
  }, []);

  const handleSummaryStageClick = useCallback((clickedStage: string) => {
    setStage(prev => (prev === clickedStage ? '' : clickedStage));
  }, []);

  const hasActiveFilters = Boolean(query || agent || status || stage);

  return (
    <div className="diagnostics-app">
      <a className="skip-link" href="#detail-panel">
        Перейти к разбору ответа
      </a>

      <Header dark={dark} onToggleTheme={toggleTheme} />

      <main className="app-main-layout">
        {error ? (
          <div className="global-error-banner" role="alert">
            <span>{error}</span>
            <button type="button" onClick={() => void refresh()} className="retry-btn">
              <RefreshCw size={14} />
              <span>Повторить</span>
            </button>
          </div>
        ) : null}

        <Toolbar
          tab={tab}
          onTabChange={handleTabChange}
          items={items}
          loading={loading}
          autoRefresh={autoRefresh}
          onAutoRefreshChange={setAutoRefresh}
          onRefresh={() => void refresh()}
        />

        {tab === 'agents' ? (
          <SummaryBar
            items={filteredItems}
            selectedStage={stage}
            onSelectStage={handleSummaryStageClick}
          />
        ) : null}

        <Filters
          tab={tab}
          query={query}
          onQueryChange={setQuery}
          agent={agent}
          onAgentChange={setAgent}
          status={status}
          onStatusChange={setStatus}
          stage={stage}
          onStageChange={setStage}
          onReset={handleResetFilters}
          hasActiveFilters={hasActiveFilters}
        />

        <div className="workspace-grid">
          <TraceList
            items={filteredItems}
            loading={loading}
            selectedId={selectedId}
            onSelect={selectTrace}
            visibleCount={visible}
            onLoadMore={() => setVisible(prev => prev + 40)}
            hasTotalItems={items.some(item => matchesTab(item, tab))}
            onResetFilters={handleResetFilters}
          />

          <section
            id="detail-panel"
            className="detail-workspace"
            aria-label="Разбор выбранного запроса"
            aria-busy={detailLoading}
            tabIndex={-1}
          >
            {detailLoading ? (
              <div className="detail-loading-state" role="status">
                <Activity size={32} className="spin accent-icon" aria-hidden="true" />
                <p>Загружаем цепочку этапов запроса…</p>
              </div>
            ) : detailError ? (
              <div className="detail-error-state" role="alert">
                <h3>Ошибка загрузки трассы</h3>
                <p>{detailError}</p>
                <button
                  type="button"
                  className="primary-action-btn"
                  onClick={reloadCurrentDetail}
                >
                  <RefreshCw size={14} />
                  <span>Повторить попытку</span>
                </button>
              </div>
            ) : trace ? (
              <TraceDetailView
                key={trace.id}
                trace={trace}
                items={items}
                onSaved={handleReviewSaved}
                tab={tab}
              />
            ) : (
              <div className="detail-empty-state">
                <Activity size={40} className="muted-icon" aria-hidden="true" />
                <h2>Выберите обращение для инспекции</h2>
                <p className="hint">
                  Нажмите на любой запрос в левом списке, чтобы изучить шаги LangGraph, поиск документов, вызовы модели и провести оценку качества.
                </p>
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
export default App;
