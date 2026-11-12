import React, { useEffect, useRef, useState, memo } from 'react';
import { ArrowUp, Loader2 } from 'lucide-react';

interface ChatInputProps {
  onSendMessage: (text: string) => void | Promise<void>;
  disabled?: boolean;
  placeholder?: string;
}

function resizeInput(input: HTMLTextAreaElement) {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

export const ChatInput: React.FC<ChatInputProps> = memo(({
  onSendMessage,
  disabled = false,
  placeholder = 'Спросите о документах или опишите задачу…',
}) => {
  const [text, setText] = useState('');
  const textarea = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (disabled) return;

    const startTyping = (event: KeyboardEvent) => {
      const input = textarea.current;
      const active = document.activeElement;
      if (!input || input.disabled || active === input || !document.hasFocus() ||
          event.defaultPrevented || event.isComposing || event.metaKey ||
          ((event.ctrlKey || event.altKey) && !event.getModifierState('AltGraph')) ||
          event.key === ' ' || [...event.key].length !== 1 ||
          (active instanceof HTMLElement && active.isContentEditable) ||
          active?.closest('input, textarea, select, [role="textbox"], [role="combobox"], [role="dialog"]')) return;

      event.preventDefault();
      input.focus({ preventScroll: true });
      const { selectionStart, selectionEnd } = input;
      if (input.value.length - (selectionEnd - selectionStart) + event.key.length <= input.maxLength) {
        input.setRangeText(event.key, selectionStart, selectionEnd, 'end');
        setText(input.value);
        resizeInput(input);
      }
    };

    document.addEventListener('keydown', startTyping);
    return () => document.removeEventListener('keydown', startTyping);
  }, [disabled]);

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    resizeInput(e.target);
  };

  const submit = () => {
    if (!disabled && text.trim()) {
      void onSendMessage(text);
      setText('');
      if (textarea.current) {
        textarea.current.style.height = 'auto';
      }
    }
  };

  return (
    <div className="composer-wrap">
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
          disabled={disabled || !text.trim()}
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
