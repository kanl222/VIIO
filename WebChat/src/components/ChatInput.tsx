import React, { useRef, useState, memo } from 'react';
import { ArrowUp, Loader2 } from 'lucide-react';

interface ChatInputProps {
  onSendMessage: (text: string) => void | Promise<void>;
  disabled?: boolean;
  placeholder?: string;
}

export const ChatInput: React.FC<ChatInputProps> = memo(({
  onSendMessage,
  disabled = false,
  placeholder = 'Спросите о документах или опишите задачу…',
}) => {
  const [text, setText] = useState('');
  const textarea = useRef<HTMLTextAreaElement>(null);

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    const target = e.target;
    target.style.height = 'auto';
    target.style.height = `${Math.min(target.scrollHeight, 180)}px`;
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
