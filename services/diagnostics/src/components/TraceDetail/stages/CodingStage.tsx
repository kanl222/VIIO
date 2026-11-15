import { memo, useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { type TraceEvent } from '../../../types';
import { copyToClipboard } from '../../../utils/formatters';
import { LazyDetails } from '../../../LazyDetails';

export const CodingStage = memo(function CodingStage({ event }: { event: TraceEvent }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async (code: string) => {
    const ok = await copyToClipboard(code);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  if (event.stage === 'coding_dispatch') {
    return (
      <div className="coding-stage-card">
        <p><strong>Задача программирования:</strong> {event.task}</p>
        {event.user ? <p className="hint">Инициатор: {event.user}</p> : null}
      </div>
    );
  }

  if (event.stage === 'coding_reference') {
    return (
      <div className="coding-stage-card">
        <p><strong>Поиск по кодовой базе / БЗ:</strong> {event.query || 'Без запроса'}</p>
        <p className="hint">
          Статус: {event.status === 'ok' ? `Найдено ${event.chars?.toLocaleString('ru-RU')} симв.` : 'Контекст не найден'}
        </p>
        {event.reference ? (
          <LazyDetails summary="Справочный фрагмент исходного кода / документации">
            {() => (
              <div className="code-container">
                <div className="code-actions">
                  <button
                    type="button"
                    className="copy-action-btn"
                    onClick={() => handleCopy(event.reference || '')}
                  >
                    {copied ? <Check size={12} className="good" /> : <Copy size={12} />}
                    <span>{copied ? 'Скопировано' : 'Копировать'}</span>
                  </button>
                </div>
                <pre className="code-block"><code>{event.reference}</code></pre>
              </div>
            )}
          </LazyDetails>
        ) : null}
      </div>
    );
  }

  if (event.stage === 'coding_generation') {
    return (
      <div className="coding-stage-card">
        <p>
          <strong>Решение кодера сформировано:</strong> {event.length?.toLocaleString('ru-RU')} символов
        </p>
        {event.answer ? (
          <LazyDetails summary="Текст решения кодера (код и пояснения)">
            {() => (
              <div className="code-container">
                <div className="code-actions">
                  <button
                    type="button"
                    className="copy-action-btn"
                    onClick={() => handleCopy(event.answer || '')}
                  >
                    {copied ? <Check size={12} className="good" /> : <Copy size={12} />}
                    <span>{copied ? 'Скопировано' : 'Копировать решение'}</span>
                  </button>
                </div>
                <div className="text coding-answer">{event.answer}</div>
              </div>
            )}
          </LazyDetails>
        ) : null}
      </div>
    );
  }

  return null;
});
