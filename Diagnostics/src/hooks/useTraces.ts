import { useState, useEffect, useRef, useCallback } from 'react';
import { api, type Trace, type TraceSummary, type Review } from '../types';

// In-memory trace detail cache across component re-renders
const traceDetailCache = new Map<string, Trace>();

export function useTraces() {
  const [items, setItems] = useState<TraceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(true);

  const [selectedId, setSelectedId] = useState('');
  const [trace, setTrace] = useState<Trace | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [detailRevision, setDetailRevision] = useState(0);

  const summaryAbortRef = useRef<AbortController | null>(null);
  const detailAbortRef = useRef<AbortController | null>(null);

  // Fetch summaries list
  const refresh = useCallback(async (quiet = false) => {
    summaryAbortRef.current?.abort();
    const controller = new AbortController();
    summaryAbortRef.current = controller;

    if (!quiet) setLoading(true);
    setError('');

    try {
      const result = await api<{ items: TraceSummary[]; retention: string }>('/api/traces', {
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setItems(result.items);
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof Error ? err.message : 'Не удалось загрузить журнал. Повторите запрос.');
      }
    } finally {
      if (summaryAbortRef.current === controller) summaryAbortRef.current = null;
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    void refresh();
    return () => summaryAbortRef.current?.abort();
  }, [refresh]);

  // Real-time SSE streaming with polling fallback
  useEffect(() => {
    if (!autoRefresh) return;
    let eventSource: EventSource | null = null;
    try {
      eventSource = new EventSource('/api/traces/stream');
      eventSource.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === 'traces_updated' || payload.type === 'review_saved') {
            void refresh(true);
          }
        } catch {
          void refresh(true);
        }
      };
      eventSource.onerror = () => {
        // EventSource automatically retries connection
      };
    } catch {}

    const timer = setInterval(() => {
      if (!document.hidden && !summaryAbortRef.current) {
        void refresh(true);
      }
    }, 4000);

    return () => {
      eventSource?.close();
      clearInterval(timer);
    };
  }, [autoRefresh, refresh]);

  // Fetch trace detail when selectedId or detailRevision changes
  useEffect(() => {
    detailAbortRef.current?.abort();

    if (!selectedId) {
      setTrace(null);
      setDetailError('');
      setDetailLoading(false);
      return;
    }

    // Check memory cache first for instant display
    const cached = traceDetailCache.get(selectedId);
    if (cached) {
      setTrace(cached);
      setDetailError('');
    } else {
      setTrace(null);
      setDetailLoading(true);
    }

    const controller = new AbortController();
    detailAbortRef.current = controller;

    api<Trace>(`/api/traces/${selectedId}`, { signal: controller.signal })
      .then(result => {
        if (!controller.signal.aborted) {
          traceDetailCache.set(selectedId, result);
          setTrace(result);
          setDetailError('');
        }
      })
      .catch(err => {
        if (!controller.signal.aborted) {
          if (!cached) setTrace(null);
          setDetailError(err instanceof Error ? err.message : 'Ошибка загрузки деталей');
        }
      })
      .finally(() => {
        if (detailAbortRef.current === controller) detailAbortRef.current = null;
        if (!controller.signal.aborted) {
          setDetailLoading(false);
        }
      });

    return () => controller.abort();
  }, [selectedId, detailRevision]);

  // Update review locally in list & cached trace
  useEffect(() => {
    if (trace?.status !== 'running') return;
    const timer = setInterval(() => {
      if (!document.hidden && !detailAbortRef.current) setDetailRevision(value => value + 1);
    }, 2000);
    return () => clearInterval(timer);
  }, [trace?.status]);

  const handleReviewSaved = useCallback((id: string, review: Review) => {
    setItems(prev => prev.map(item => (item.id === id ? { ...item, review } : item)));

    setTrace(prev => {
      if (prev?.id === id) {
        const updated = { ...prev, review };
        traceDetailCache.set(id, updated);
        return updated;
      }
      return prev;
    });

    const cached = traceDetailCache.get(id);
    if (cached) {
      traceDetailCache.set(id, { ...cached, review });
    }
  }, []);

  const selectTrace = useCallback((id: string) => {
    setSelectedId(id);
    if (window.matchMedia('(max-width: 760px)').matches) {
      document.getElementById('detail')?.scrollIntoView({ block: 'start' });
    }
  }, []);

  const reloadCurrentDetail = useCallback(() => {
    setDetailRevision(r => r + 1);
  }, []);

  return {
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
  };
}
