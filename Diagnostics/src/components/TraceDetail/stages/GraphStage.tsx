import { memo } from 'react';
import { Network, Compass, FileText, CheckCircle2, AlertCircle } from 'lucide-react';
import { type TraceEvent } from '../../../types';
import { LazyDetails } from '../../../LazyDetails';

export const GraphStage = memo(function GraphStage({ event }: { event: TraceEvent }) {
  const isError = event.status === 'error';
  const menus = (event.menus as string[]) || [];
  const operations = (event.operations as string[]) || [];
  const sources = (event.sources as { id?: string; document_id?: string; title: string }[]) || [];

  return (
    <div className="db-stage-card">
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <Network size={16} className="accent-icon" />
        <strong>Графовый поиск:</strong> <code>{String(event.query || '—')}</code>
        {isError ? (
          <span className="badge-highlight bad" style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
            <AlertCircle size={12} /> Ошибка
          </span>
        ) : (
          <span className="badge-highlight good" style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
            <CheckCircle2 size={12} /> Успешно
          </span>
        )}
      </div>

      {isError && event.error ? (
        <p className="bad" style={{ margin: '4px 0' }}>{String(event.error)}</p>
      ) : null}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', margin: '8px 0' }}>
        <span className="badge-highlight">
          Пути меню: <strong>{Number(event.menus_count ?? menus.length)}</strong>
        </span>
        <span className="badge-highlight">
          Операции: <strong>{Number(event.operations_count ?? operations.length)}</strong>
        </span>
        <span className="badge-highlight">
          Условия: <strong>{Number(event.conditions_count ?? 0)}</strong>
        </span>
        <span className="badge-highlight">
          Роли: <strong>{Number(event.roles_count ?? 0)}</strong>
        </span>
        <span className="badge-highlight good">
          Связанные документы: <strong>{Number(event.sources_count ?? sources.length)}</strong>
        </span>
      </div>

      {menus.length > 0 ? (
        <div style={{ marginTop: '10px' }}>
          <h4 style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', margin: '6px 0' }}>
            <Compass size={14} /> Найденные разделы меню:
          </h4>
          <ul style={{ margin: '4px 0 8px 16px', padding: 0, fontSize: '12px', lineHeight: '1.6' }}>
            {menus.map((m, idx) => (
              <li key={idx} style={{ color: 'var(--text-primary, #333)' }}>
                <code>{m}</code>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {operations.length > 0 ? (
        <div style={{ marginTop: '8px' }}>
          <h4 style={{ fontSize: '13px', margin: '6px 0' }}>Операции:</h4>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
            {operations.map((op, idx) => (
              <span key={idx} className="badge-highlight" style={{ fontSize: '11px' }}>
                {op}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {sources.length > 0 ? (
        <div style={{ marginTop: '10px' }}>
          <h4 style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', margin: '6px 0' }}>
            <FileText size={14} /> Подтверждающие регламенты графа:
          </h4>
          <ul style={{ margin: '4px 0 8px 16px', padding: 0, fontSize: '12px', lineHeight: '1.6' }}>
            {sources.map((s, idx) => (
              <li key={idx}>
                <strong>{s.title}</strong> {s.document_id || s.id ? <span className="hint">({s.document_id || s.id})</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {event.context ? (
        <div style={{ marginTop: '10px' }}>
          <LazyDetails summary="Сформированный контекст графа (Graph Context)">
            {() => (
              <pre className="code-block" style={{ maxHeight: '250px', overflowY: 'auto', fontSize: '11px' }}>
                {String(event.context)}
              </pre>
            )}
          </LazyDetails>
        </div>
      ) : null}
    </div>
  );
});
