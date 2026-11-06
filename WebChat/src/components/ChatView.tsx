import React, { useEffect, useRef, memo } from 'react';
import { ArrowUpRight, BookOpen, FileText, ListChecks, Loader2 } from 'lucide-react';
import type { Message } from '../types';
import { ChatMessage } from './ChatMessage';
import type { TaskStep } from '../api/chat';

interface ChatViewProps {
  messages: Message[];
  loading: boolean;
  submitting: boolean;
  plan?: TaskStep[];
  progressText?: string;
  conversationTitle?: string;
  onQuickPrompt?: (text: string) => void;
}

const PROMPTS = [
  { title: 'Найти в документах', text: 'Найди в базе знаний правила работы с клиентами', detail: 'Регламенты и инструкции', icon: BookOpen },
  { title: 'Разобраться в вопросе', text: 'Объясни, какие документы есть в базе знаний и как с ними работать', detail: 'Понятные объяснения', icon: FileText },
  { title: 'Составить план', text: 'Помоги составить пошаговый план решения рабочей задачи. Сначала уточни детали.', detail: 'От вопроса к следующим шагам', icon: ListChecks },
];

export const ChatView: React.FC<ChatViewProps> = memo(({
  messages,
  loading,
  submitting,
  plan = [],
  progressText,
  conversationTitle,
  onQuickPrompt,
}) => {
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const reducedMotion = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    bottom.current?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth' });
  }, [messages, submitting, plan]);

  if (loading) {
    return (
      <div className="loading-state chat-loading" role="status">
        <Loader2 className="animate-spin" size={22} />
        Загружаем диалог…
      </div>
    );
  }

  if (!messages.length) {
    return (
      <div className="chat-welcome">
        <div className="welcome-content">
          <span className="welcome-mark" aria-hidden="true">V</span>
          <h1>{conversationTitle && conversationTitle !== 'Новый диалог' ? conversationTitle : 'С чего начнём?'}</h1>
          <p>
            Задайте вопрос, найдите нужное в документах<br className="hidden sm:block" /> или разберите задачу вместе с ассистентом.
          </p>
          <div className="prompt-list">
            {PROMPTS.map(({ title, text, detail, icon: Icon }) => (
              <button key={title} onClick={() => onQuickPrompt?.(text)} disabled={submitting}>
                <Icon size={21} aria-hidden="true" />
                <span>
                  <strong>{title}</strong>
                  <small>{detail}</small>
                </span>
                <ArrowUpRight size={17} aria-hidden="true" />
              </button>
            ))}
          </div>
          <span className="welcome-note">Ваши диалоги видны только вам.</span>
        </div>
      </div>
    );
  }

  return (
    <div className="message-scroll">
      <div className="message-column">
        {messages.map((message) => (
          <ChatMessage key={message.id} message={message} />
        ))}
        {submitting && (
          <div>
          {plan.length > 0 && <ol className="task-plan" aria-label="План выполнения задачи">{plan.map((step, index) =>
            <li key={index} data-status={step.status}>
              <span aria-hidden="true">{step.status === 'completed' ? '✓' : step.status === 'in_progress' ? '●' : '○'}</span>
              {step.title}<span className="sr-only">{step.status === 'completed' ? ' — выполнено' : step.status === 'in_progress' ? ' — выполняется' : ' — ожидает'}</span>
            </li>)}</ol>}
          <div className="assistant-thinking" role="status">
            <Loader2 size={17} className="animate-spin" />
            <span>{progressText || 'Ассистент готовит ответ…'}</span>
          </div>
          </div>
        )}
        <div ref={bottom} />
      </div>
    </div>
  );
});

ChatView.displayName = 'ChatView';
