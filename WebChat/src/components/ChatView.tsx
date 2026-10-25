import { useEffect, useRef } from 'react';
import { ArrowUpRight, BookOpen, FileText, ListChecks, Loader2 } from 'lucide-react';
import type { Message } from '../types';
import { ChatMessage } from './ChatMessage';
interface ChatViewProps { messages: Message[]; loading: boolean; submitting: boolean; conversationTitle?: string; onQuickPrompt?: (text: string) => void }
const prompts = [
  { title: 'Найти в документах', text: 'Найди в базе знаний правила работы с клиентами', detail: 'Регламенты и инструкции', icon: BookOpen },
  { title: 'Разобраться в вопросе', text: 'Объясни, какие документы есть в базе знаний и как с ними работать', detail: 'Понятные объяснения', icon: FileText },
  { title: 'Составить план', text: 'Помоги составить пошаговый план решения рабочей задачи. Сначала уточни детали.', detail: 'От вопроса к следующим шагам', icon: ListChecks },
];
export function ChatView({ messages, loading, submitting, conversationTitle, onQuickPrompt }: ChatViewProps) {
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); }, [messages, submitting]);
  if (loading) return <div className="loading-state chat-loading" role="status"><Loader2 className="animate-spin" size={22} />Загружаем диалог…</div>;
  if (!messages.length) return <div className="chat-welcome"><div className="welcome-content"><span className="welcome-mark" aria-hidden="true">V</span>
    <h1>{conversationTitle && conversationTitle !== 'Новый диалог' ? conversationTitle : 'С чего начнём?'}</h1>
    <p>Задайте вопрос, найдите нужное в документах<br className="hidden sm:block" /> или разберите задачу вместе с ассистентом.</p>
    <div className="prompt-list">{prompts.map(({ title, text, detail, icon: Icon }) => <button key={title} onClick={() => onQuickPrompt?.(text)} disabled={submitting}><Icon size={21} aria-hidden="true" /><span><strong>{title}</strong><small>{detail}</small></span><ArrowUpRight size={17} aria-hidden="true" /></button>)}</div>
    <span className="welcome-note">Ваши диалоги видны только вам.</span>
  </div></div>;
  return <div className="message-scroll"><div className="message-column">{messages.map(message => <ChatMessage key={message.id} message={message} />)}
    {submitting && <div className="assistant-thinking" role="status"><Loader2 size={17} className="animate-spin" /><span>Ассистент готовит ответ…</span></div>}<div ref={bottom} /></div></div>;
}
