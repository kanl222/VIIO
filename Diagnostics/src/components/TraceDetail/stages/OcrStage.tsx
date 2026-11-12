import { memo, useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { type TraceEvent, text } from '../../../types';
import { formatBytes, copyToClipboard } from '../../../utils/formatters';
import { LazyDetails } from '../../../LazyDetails';

export const OcrStage = memo(function OcrStage({ event }: { event: TraceEvent }) {
  const [copiedText, setCopiedText] = useState(false);

  const copyExtracted = async (content: string) => {
    const ok = await copyToClipboard(content);
    if (ok) {
      setCopiedText(true);
      setTimeout(() => setCopiedText(false), 1500);
    }
  };

  if (event.stage === 'ocr_request') {
    return (
      <dl className="route-data ocr-params">
        <dt>Движок обработки:</dt>
        <dd><strong>{event.engine || 'Авто'}</strong></dd>
        <dt>Размер оригинала:</dt>
        <dd>{formatBytes(event.size_bytes)} ({event.size_bytes?.toLocaleString('ru-RU')} Б)</dd>
        <dt>Языки распознавания:</dt>
        <dd>{event.languages?.join(', ') || 'Русский'}</dd>
        <dt>Лимиты:</dt>
        <dd>до {event.max_pages} стр. · до {event.max_chars?.toLocaleString('ru-RU')} симв. · таймаут {event.timeout_seconds} с</dd>
        <dt>SHA-256 файла:</dt>
        <dd className="file-digest"><code>{event.sha256}</code></dd>
      </dl>
    );
  }

  if (event.stage === 'ocr_result') {
    const isError = event.status === 'error';
    const contentString = text(event.content);

    return (
      <div className="ocr-result-stage">
        <div className="status-banner">
          <span className={`status-pill ${isError ? 'bad-pill' : 'good-pill'}`}>
            {isError ? 'Ошибка распознавания PDF' : '✓ Документ успешно обработан'}
            {event.engine ? ` (${event.engine})` : ''}
            {event.pages !== undefined ? ` · ${event.pages} стр.` : ''}
          </span>
        </div>

        {event.error ? (
          <p className="bad error-notice">
            {event.status_code ? `[HTTP ${event.status_code}] ` : ''}
            {event.detail || event.error}
          </p>
        ) : null}

        {event.content !== undefined ? (
          <div className="extracted-text-section">
            <div className="section-head-with-copy">
              <h4>Извлечённый текст документа:</h4>
              <button
                type="button"
                className="copy-action-btn"
                onClick={() => copyExtracted(contentString)}
              >
                {copiedText ? <Check size={12} className="good" /> : <Copy size={12} />}
                <span>{copiedText ? 'Текст скопирован' : 'Копировать весь текст'}</span>
              </button>
            </div>
            <div className="text fragment-text">{contentString}</div>
          </div>
        ) : null}

        {event.document !== undefined ? (
          <LazyDetails summary="Структурные данные Docling (таблицы, секции, разметка JSON)">
            {() => <pre className="code-block">{text(event.document)}</pre>}
          </LazyDetails>
        ) : (
          <p className="hint">Структурная разметка документа не сохранялась.</p>
        )}
      </div>
    );
  }

  return null;
});
