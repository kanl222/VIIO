import { memo, useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { type TraceEvent } from '../../../types';
import { copyToClipboard } from '../../../utils/formatters';
import { LazyDetails } from '../../../LazyDetails';

function CopyButton({ textToCopy, label = 'Копировать' }: { textToCopy: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    const ok = await copyToClipboard(textToCopy);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <button type="button" className="copy-action-btn" onClick={handleCopy} title={label}>
      {copied ? <Check size={12} className="good" /> : <Copy size={12} />}
      <span>{copied ? 'Скопировано' : label}</span>
    </button>
  );
}

export const DatabaseStage = memo(function DatabaseStage({ event }: { event: TraceEvent }) {
  if (event.stage === 'db_inspect_schema') {
    return (
      <div className="db-stage-card">
        <p><strong>Таблицы БД:</strong> {event.tables?.length ? event.tables.join(', ') : 'Не указаны'}</p>
        <p className="hint">Размер схемы: {event.length?.toLocaleString('ru-RU')} симв.</p>
        {event.schema ? (
          <LazyDetails summary="Схема выбранных таблиц (DDL)">
            {() => (
              <div className="code-container">
                <div className="code-actions">
                  <CopyButton textToCopy={event.schema || ''} label="Скопировать DDL" />
                </div>
                <pre className="code-block">{event.schema}</pre>
              </div>
            )}
          </LazyDetails>
        ) : null}
      </div>
    );
  }

  if (event.stage === 'db_optimize_query') {
    return (
      <div className="db-stage-card">
        <p><strong>Запрос на оптимизацию:</strong></p>
        {event.sql ? (
          <div className="code-container">
            <div className="code-actions">
              <CopyButton textToCopy={event.sql} label="Копировать SQL" />
            </div>
            <pre className="code-block"><code>{event.sql}</code></pre>
          </div>
        ) : null}
        <h4>План выполнения (EXPLAIN ANALYZE)</h4>
        <pre className="code-block">{event.plan || 'План не получен'}</pre>
        {event.tables?.length ? <p className="hint">Таблицы: {event.tables.join(', ')}</p> : null}
      </div>
    );
  }

  if (event.stage === 'db_diagnose_error') {
    return (
      <div className="db-stage-card">
        <p className="bad"><strong>Диагностика ошибки:</strong> {event.error || 'Ошибка не указана'}</p>
        {event.tables?.length ? <p className="hint">Связанные таблицы: {event.tables.join(', ')}</p> : null}
      </div>
    );
  }

  if (event.stage === 'db_generate_sql') {
    const sqlText = event.sql || event.query || '';
    return (
      <div className="db-stage-card">
        <div className="code-container">
          <div className="code-header">
            <strong>Сгенерированный SQL-запрос:</strong>
            <CopyButton textToCopy={sqlText} label="Копировать SQL" />
          </div>
          <pre className="code-block"><code>{sqlText}</code></pre>
        </div>
        {event.tables?.length ? <p className="hint">Целевые таблицы: {event.tables.join(', ')}</p> : null}
      </div>
    );
  }

  if (event.stage === 'db_explain_check') {
    const isValid = event.valid === true;
    return (
      <div className="db-stage-card">
        <div className="status-banner">
          <span className={`status-pill ${isValid ? 'good-pill' : 'bad-pill'}`}>
            {isValid ? '✓ План запроса валиден (Explain Check OK)' : '✗ План некорректен (Требуется автоисправление)'}
          </span>
        </div>
        {event.sql ? (
          <div className="code-container">
            <div className="code-actions">
              <CopyButton textToCopy={event.sql} label="Копировать SQL" />
            </div>
            <pre className="code-block"><code>{event.sql}</code></pre>
          </div>
        ) : null}
        {event.error ? <p className="bad"><strong>Ошибка PostgreSQL:</strong> {event.error}</p> : null}
        {(event.plan || event.plan_snippet) ? (
          <LazyDetails summary="План выполнения запроса (EXPLAIN)">
            {() => <pre className="code-block">{event.plan || event.plan_snippet}</pre>}
          </LazyDetails>
        ) : null}
      </div>
    );
  }

  if (event.stage === 'db_fix_sql') {
    return (
      <div className="db-stage-card">
        <p className="bad"><strong>Цикл автоисправления SQL (попытка #{event.attempt})</strong></p>
        {event.error ? <p className="bad">Причина сбоя: {event.error}</p> : null}
        <div className="diff-view">
          <div className="diff-column">
            <h4>Было (ошибочный SQL):</h4>
            <pre className="code-block bad-code"><code>{event.old_sql}</code></pre>
          </div>
          <div className="diff-column">
            <h4>Стало (исправленный SQL):</h4>
            <div className="code-actions">
              <CopyButton textToCopy={event.fixed_sql || ''} label="Копировать" />
            </div>
            <pre className="code-block good-code"><code>{event.fixed_sql}</code></pre>
          </div>
        </div>
      </div>
    );
  }

  if (event.stage === 'db_execute_sql') {
    return (
      <div className="db-stage-card">
        <p className="good">
          <strong>✓ SQL выполнен успешно.</strong> Длина результата: {event.length?.toLocaleString('ru-RU')} байт
        </p>
        {event.sql ? (
          <div className="code-container">
            <div className="code-actions">
              <CopyButton textToCopy={event.sql} label="Копировать SQL" />
            </div>
            <pre className="code-block"><code>{event.sql}</code></pre>
          </div>
        ) : null}
        {event.result ? (
          <LazyDetails summary="Фрагмент возвращенных данных (JSON / Кортежи)">
            {() => <pre className="code-block">{event.result}</pre>}
          </LazyDetails>
        ) : null}
      </div>
    );
  }

  if (event.stage === 'db_format_answer') {
    return (
      <div className="db-stage-card">
        <p><strong>Форматирование ответа:</strong> {event.length?.toLocaleString('ru-RU')} символов</p>
        {event.answer ? (
          <LazyDetails summary="Сформированный итоговый ответ">
            {() => <div className="text">{event.answer}</div>}
          </LazyDetails>
        ) : null}
      </div>
    );
  }

  return null;
});
