import React, { useEffect, useRef, useState, memo } from 'react';
import { ArrowUp, Loader2, Paperclip, FileText, X } from 'lucide-react';
import type { MessageAttachment } from '../types';
import type { RagOptions } from '../api/chat';

const useCallback: <T extends (...args: any[]) => any>(callback: T, deps: any[]) => T =
  React.useCallback ?? ((fn: any) => fn);

interface ChatInputProps {
  onSendMessage: (text: string, options?: RagOptions, attachments?: MessageAttachment[]) => void | Promise<void>;
  onCancel?: () => void;
  disabled?: boolean;
  placeholder?: string;
}

function resizeInput(input: HTMLTextAreaElement) {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

function formatSize(bytes?: number): string {
  if (bytes === undefined) return '';
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

function readFileAsBase64(file: File): Promise<MessageAttachment> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || '');
      const base64 = result.includes(',') ? result.split(',')[1] : result;
      resolve({
        name: file.name,
        type: file.type || 'application/octet-stream',
        size: file.size,
        content_base64: base64,
      });
    };
    reader.onerror = () => reject(new Error(`Не удалось прочитать файл ${file.name}`));
    reader.readAsDataURL(file);
  });
}

const ACCEPTED_EXTENSIONS = '.pdf,.docx,.txt,.md,.py,.sql,.js,.ts,.tsx,.jsx,.json,.yaml,.yml,.xml,.html,.css,.cs,.java,.php,.go,.rs,.sh,.toml,.png,.jpg,.jpeg,.webp';

export const ChatInput: React.FC<ChatInputProps> = memo(({
  onSendMessage,
  disabled = false,
  onCancel,
  placeholder = 'Спросите о документах или опишите задачу…',
}) => {
  const [text, setText] = useState('');
  const [attachments, setAttachments] = useState<MessageAttachment[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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

  const handleAddFiles = useCallback(async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    try {
      const loaded = await Promise.all(list.map(readFileAsBase64));
      setAttachments((prev) => [...prev, ...loaded]);
    } catch (err) {
      console.error('[ChatInput] Ошибка чтения вложений:', err);
    }
  }, []);

  const handleRemoveAttachment = useCallback((index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const submit = () => {
    const trimmed = text.trim();
    const message = trimmed || 'Анализ прикреплённых файлов';
    if (!disabled && (trimmed || attachments.length > 0)) {
      if (attachments.length > 0) {
        void onSendMessage(message, undefined, attachments);
      } else {
        void onSendMessage(message);
      }
      setText('');
      setAttachments([]);
      if (fileInputRef.current) fileInputRef.current.value = '';
      if (textarea.current) {
        textarea.current.style.height = 'auto';
      }
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    if (!disabled) setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (!disabled && e.dataTransfer.files?.length) {
      void handleAddFiles(e.dataTransfer.files);
    }
  };

  return (
    <div
      className={`composer-wrap ${isDragging ? 'ring-2 ring-indigo-500 rounded-xl transition-all' : ''}`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Список прикреплённых файлов */}
      {onCancel && <button type="button" onClick={onCancel}
        className="mb-2 rounded-lg border border-zinc-300 px-3 py-2 dark:border-zinc-700">
        Остановить ответ
      </button>}
      <div className="composer">
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 px-3 pt-3">
            {attachments.map((att, idx) => (
              <div
                key={idx}
                className="inline-flex min-w-0 max-w-full items-center gap-1.5 px-2.5 py-1 text-xs bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-md shadow-xs text-zinc-800 dark:text-zinc-200"
              >
                <FileText size={14} className="text-indigo-500 shrink-0" />
                <span className="min-w-0 max-w-[160px] truncate font-medium" title={att.name}>{att.name}</span>
                {att.size !== undefined && <span className="shrink-0 text-[10px] text-zinc-400 font-mono">({formatSize(att.size)})</span>}
                <button
                  type="button"
                  onClick={() => handleRemoveAttachment(idx)}
                  className="ml-1 shrink-0 p-0.5 text-zinc-400 hover:text-red-500 dark:hover:text-red-400 rounded transition"
                  title="Удалить файл"
                  disabled={disabled}
                >
                  <X size={13} />
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="composer-row">
          {/* Скрытый input для выбора файлов */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            accept={ACCEPTED_EXTENSIONS}
            onChange={(e) => {
              if (e.target.files) void handleAddFiles(e.target.files);
              e.target.value = '';
            }}
          />

          {/* Кнопка скрепки для вложений */}
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled}
            className="p-2 text-zinc-400 hover:text-indigo-600 dark:hover:text-indigo-400 rounded-lg transition shrink-0"
            title="Прикрепить файлы (документы, код, изображения)"
            aria-label="Прикрепить файл"
          >
            <Paperclip size={19} />
          </button>

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
            disabled={disabled || (!text.trim() && attachments.length === 0)}
            onClick={submit}
            aria-label="Отправить сообщение"
          >
            {disabled ? <Loader2 size={20} className="animate-spin" /> : <ArrowUp size={20} />}
          </button>
        </div>
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

ChatInput.displayName = 'ChatInput';
