import { memo, useState } from 'react';
import { Copy, Check, ChevronDown, ChevronUp, BookOpen } from 'lucide-react';
import { type TraceEvent } from '../../types';
import { copyToClipboard } from '../../utils/formatters';

interface AnswerStageProps {
  event?: TraceEvent;
}

export const AnswerStage = memo(function AnswerStage({ event }: AnswerStageProps) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const reply = event?.reply?.trim() || '';
  const isLong = reply.length > 700;
  const sources = event?.sources || [];

  const handleCopy = async () => {
    if (!reply) return;
    const ok = await copyToClipboard(reply);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <section className="answer-section" aria-label="Итоговый ответ пользователю">
      <div className="answer-header">
        <div className="answer-title-group">
          <BookOpen size={18} className="accent-icon" aria-hidden="true" />
          <h3>Ответ пользователю</h3>
        </div>
        {reply ? (
          <button
            type="button"
            className="copy-action-btn"
            onClick={handleCopy}
            title="Скопировать ответ в буфер обмена"
          >
            {copied ? <Check size={13} className="good" /> : <Copy size={13} />}
            <span>{copied ? 'Скопировано' : 'Копировать ответ'}</span>
          </button>
        ) : null}
      </div>

      {reply ? (
        <>
          <div className={`answer-text text ${!expanded && isLong ? 'answer-preview' : ''}`}>
            {reply}
          </div>

          {isLong ? (
            <button
              type="button"
              className="answer-toggle-btn"
              onClick={() => setExpanded(prev => !prev)}
              aria-expanded={expanded}
            >
              {expanded ? (
                <>
                  <ChevronUp size={14} aria-hidden="true" />
                  <span>Свернуть ответ</span>
                </>
              ) : (
                <>
                  <ChevronDown size={14} aria-hidden="true" />
                  <span>Читать ответ полностью ({reply.length.toLocaleString('ru-RU')} симв.)</span>
                </>
              )}
            </button>
          ) : null}
        </>
      ) : (
        <p className="hint empty-answer">Ответ в трассе отсутствует (возможно, произошёл сбой до формирования ответа).</p>
      )}

      {sources.length > 0 ? (
        <div className="answer-sources">
          <span className="sources-label">Использованные источники:</span>
          <div className="sources-list">
            {sources.map((source, idx) => (
              <span key={idx} className="source-pill" title={source.document_id || source.publication_id}>
                {source.title || 'Документ без названия'}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <p className="hint sources-empty">Источники к ответу не приложены.</p>
      )}
    </section>
  );
});
