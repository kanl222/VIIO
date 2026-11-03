import React, { useRef, useState, memo } from 'react';
import { ArrowUp, Loader2 } from 'lucide-react';
import type { RagOptions } from '../api/chat';

interface ChatInputProps {
  onSendMessage: (text: string, options?: RagOptions) => void | Promise<void>;
  disabled?: boolean;
  placeholder?: string;
}

export const ChatInput: React.FC<ChatInputProps> = memo(({
  onSendMessage,
  disabled = false,
  placeholder = 'Спросите о документах или опишите задачу…',
}) => {
  const [text, setText] = useState('');
  const [ragBackend, setRagBackend] = useState<'local' | 'yandex'>('local');
  const [publicConfirmed, setPublicConfirmed] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    const target = e.target;
    target.style.height = 'auto';
    target.style.height = `${Math.min(target.scrollHeight, 180)}px`;
  };

  const submit = () => {
    if (!disabled && text.trim() && (ragBackend === 'local' || publicConfirmed)) {
      void onSendMessage(text, { rag_backend: ragBackend, confirmed_public_for_external_cloud: publicConfirmed });
      setText('');
      setPublicConfirmed(false);
      if (textarea.current) {
        textarea.current.style.height = 'auto';
      }
    }
  };

  return (
    <div className="composer-wrap">
      <div className="knowledge-mode">
        <label>База знаний <select aria-label="База знаний" disabled={disabled} value={ragBackend}
          onChange={event => { setRagBackend(event.target.value as 'local' | 'yandex'); setPublicConfirmed(false); }}>
          <option value="local">Локальная БЗ</option>
          <option value="yandex">Yandex RAG · публичные документы</option>
        </select></label>
        {ragBackend === 'yandex' && <label className="cloud-confirmation">
          <input type="checkbox" checked={publicConfirmed} disabled={disabled}
            onChange={event => setPublicConfirmed(event.target.checked)} />
          <span>Вопрос содержит только публичные данные. Разрешаю отправку в Yandex Cloud.
            <small>Передаётся только этот вопрос. История и личная память остаются в VIIO.</small></span>
        </label>}
      </div>
      <div className="composer">
        <textarea
          ref={textarea}
          aria-label="Сообщение ассистенту"
          rows={1}
          value={text}
          disabled={disabled}
          maxLength={4000}
          onChange={handleInput}
          placeholder={placeholder}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <button
          className="send-button"
          disabled={disabled || !text.trim() || (ragBackend === 'yandex' && !publicConfirmed)}
          onClick={submit}
          aria-label="Отправить сообщение"
        >
          {disabled ? <Loader2 size={20} className="animate-spin" /> : <ArrowUp size={20} />}
        </button>
      </div>
      <div className="composer-footer">
        <span>Enter — отправить · Shift + Enter — новая строка</span>
        <span>{text.length} / 4 000</span>
      </div>
      <p className="composer-disclaimer">Ассистент может ошибаться. Проверяйте важную информацию.</p>
    </div>
  );
});

ChatInput.displayName = 'ChatInput';
