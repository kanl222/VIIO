import React, { useState, memo } from 'react';
import { User, Bot, Copy, Check, Info, Sparkles, FileText, ThumbsUp, ThumbsDown } from 'lucide-react';
import type { Message } from '../types';

interface ChatMessageProps {
  message: Message;
}

// Вынесенные регулярные выражения во избежание повторной компиляции на каждую строку/рендер (js-hoist-regexp)
const DOWNLOAD_PATH = String.raw`/api/chat/download/doc-(?:[0-9a-f]{32}|[0-9a-f]{8})`;
const INLINE_REGEX = new RegExp(
  String.raw`\[([^\]\n]+)\]\((${DOWNLOAD_PATH})\)|(?<![\w/])(${DOWNLOAD_PATH})(?![\w./?#%-])|` +
  /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/.source,
  'g',
);
const CODE_BLOCK_REGEX = /```([\s\S]*?)```/g;
const ORDERED_REGEX = /^(\d+)\.\s+(.*)$/;
const FENCE_REGEX = /^\s*(`{3,}|~{3,})/;
const NORMALIZE_BULLET_REGEX = /^(?:[-*•]|\d+[.)])\s+/;
const NORMALIZE_DOC_REGEX = /^(?:материал|документ)\s+/i;
const NORMALIZE_CLEAN_REGEX = /[«»“”"'`*_]/g;
const NORMALIZE_TRAILING_PUNCT = /[.;:,]+$/;
const NORMALIZE_SPACES_REGEX = /\s+/g;
const HEADING_STRIP_HASH = /^#{1,6}\s*/;
const HEADING_STRIP_FORMAT = /[*_]/g;
const HEADING_STRIP_COLON = /:\s*$/;

const KNOWN_HEADINGS = new Set([
  'источник',
  'источники',
  'материалы',
  'предоставленные материалы',
  'подтверждающие материалы',
  'документы, подтверждающие ответ',
]);

const CodeBlock: React.FC<{ raw: string }> = memo(({ raw }) => {
  const [copied, setCopied] = useState(false);

  // Первая строка может определять язык
  const firstNewline = raw.indexOf('\n');
  let lang = 'code';
  let code = raw;

  if (firstNewline !== -1) {
    const potentialLang = raw.slice(0, firstNewline).trim();
    if (potentialLang && !potentialLang.includes(' ')) {
      lang = potentialLang;
      code = raw.slice(firstNewline + 1);
    }
  }

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="my-2.5 rounded-lg overflow-hidden border border-zinc-700 bg-zinc-950 text-zinc-100 font-mono text-xs shadow-sm">
      <div className="flex items-center justify-between px-3 py-1.5 bg-zinc-800/90 border-b border-zinc-700/60 text-[11px] text-zinc-400 select-none">
        <span className="font-semibold uppercase tracking-wider">{lang}</span>
        <button
          onClick={handleCopy}
          className="flex items-center space-x-1 px-1.5 py-0.5 rounded hover:bg-zinc-700 hover:text-zinc-100 transition"
          title="Скопировать код"
        >
          {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
          <span>{copied ? 'Скопировано!' : 'Копировать'}</span>
        </button>
      </div>
      <pre className="p-3 overflow-x-auto whitespace-pre font-mono leading-relaxed text-zinc-200">
        <code>{code}</code>
      </pre>
    </div>
  );
});

CodeBlock.displayName = 'CodeBlock';

function renderInline(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  INLINE_REGEX.lastIndex = 0;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = INLINE_REGEX.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    const downloadUrl = match[2] || match[3];
    if (downloadUrl) {
      parts.push(
        <a key={match.index} href={downloadUrl} className="underline text-indigo-600 dark:text-indigo-300">
          {match[1] || 'Скачать документ'}
        </a>
      );
    } else if (token.startsWith('`') && token.endsWith('`')) {
      parts.push(
        <code
          key={match.index}
          className="px-1 py-0.5 rounded bg-zinc-200 dark:bg-zinc-800 font-mono text-[11px] text-pink-600 dark:text-pink-400"
        >
          {token.slice(1, -1)}
        </code>
      );
    } else if (token.startsWith('**') && token.endsWith('**')) {
      parts.push(
        <strong key={match.index} className="font-semibold">
          {token.slice(2, -2)}
        </strong>
      );
    } else if (token.startsWith('*') && token.endsWith('*')) {
      parts.push(
        <em key={match.index} className="italic">
          {token.slice(1, -1)}
        </em>
      );
    }
    lastIndex = match.index + token.length;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts.length > 0 ? parts : text;
}

function isTableSeparator(line: string): boolean {
  return /^\|?\s*:?-+:?\s*(\|?\s*:?-+:?\s*)+\|?$/.test(line.trim());
}

function parseTableRow(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
}

function renderTextLines(section: string, keyPrefix: string): React.ReactNode {
  const lines = section.split('\n');
  const elements: React.ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // Таблицы Markdown
    if (trimmed.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const headers = parseTableRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim().includes('|') && lines[i].trim() !== '') {
        rows.push(parseTableRow(lines[i]));
        i++;
      }
      elements.push(
        <div key={`table-${i}`} className="my-2.5 overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-700/80 shadow-xs">
          <table className="min-w-full divide-y divide-zinc-200 dark:divide-zinc-700 text-xs">
            <thead className="bg-zinc-100 dark:bg-zinc-800/80 font-semibold text-zinc-900 dark:text-zinc-100">
              <tr>
                {headers.map((h, colIdx) => (
                  <th key={colIdx} className="px-3 py-2 text-left font-medium">
                    {renderInline(h)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800/60 bg-white dark:bg-zinc-900">
              {rows.map((row, rIdx) => (
                <tr key={rIdx} className="hover:bg-zinc-50 dark:hover:bg-zinc-800/40">
                  {row.map((cell, cIdx) => (
                    <td key={cIdx} className="px-3 py-1.5 text-zinc-700 dark:text-zinc-300">
                      {renderInline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    if (!trimmed) {
      elements.push(<div key={i} className="h-1.5" />);
      i++;
      continue;
    }
    if (line.startsWith('### ')) {
      elements.push(
        <h3 key={i} className="text-xs font-bold text-gray-900 dark:text-zinc-100 mt-2 mb-1">
          {renderInline(line.slice(4))}
        </h3>
      );
      i++;
      continue;
    }
    if (line.startsWith('## ')) {
      elements.push(
        <h2 key={i} className="text-sm font-bold text-gray-900 dark:text-zinc-100 mt-2.5 mb-1 border-b border-gray-200 dark:border-zinc-800 pb-0.5">
          {renderInline(line.slice(3))}
        </h2>
      );
      i++;
      continue;
    }
    if (line.startsWith('# ')) {
      elements.push(
        <h1 key={i} className="text-base font-extrabold text-gray-900 dark:text-zinc-100 mt-3 mb-1.5 border-b border-gray-200 dark:border-zinc-800 pb-1">
          {renderInline(line.slice(2))}
        </h1>
      );
      i++;
      continue;
    }
    if (line.startsWith('> ')) {
      elements.push(
        <blockquote
          key={i}
          className="border-l-2 border-indigo-500 pl-2.5 italic text-gray-600 dark:text-zinc-400 my-1 text-xs"
        >
          {renderInline(line.slice(2))}
        </blockquote>
      );
      i++;
      continue;
    }
    if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
      elements.push(
        <div key={i} className="flex items-start space-x-1.5 ml-2 text-xs">
          <span className="text-indigo-500 shrink-0 select-none">•</span>
          <span>{renderInline(trimmed.slice(2))}</span>
        </div>
      );
      i++;
      continue;
    }
    const orderedMatch = trimmed.match(ORDERED_REGEX);
    if (orderedMatch) {
      elements.push(
        <div key={i} className="flex items-start space-x-1.5 ml-2 text-xs">
          <span className="font-semibold text-indigo-500 shrink-0 select-none">{orderedMatch[1]}.</span>
          <span>{renderInline(orderedMatch[2])}</span>
        </div>
      );
      i++;
      continue;
    }

    elements.push(
      <p key={i} className="text-xs leading-relaxed">
        {renderInline(line)}
      </p>
    );
    i++;
  }

  return <div key={keyPrefix} className="space-y-1">{elements}</div>;
}

function renderMarkdown(content: string): React.ReactNode {
  CODE_BLOCK_REGEX.lastIndex = 0;
  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = CODE_BLOCK_REGEX.exec(content)) !== null) {
    if (match.index > lastIndex) {
      const textSection = content.slice(lastIndex, match.index);
      nodes.push(renderTextLines(textSection, `text-${lastIndex}`));
    }
    nodes.push(<CodeBlock key={`code-${match.index}`} raw={match[1]} />);
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < content.length) {
    nodes.push(renderTextLines(content.slice(lastIndex), `text-${lastIndex}`));
  }

  return nodes;
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

/** Hide a redundant terminal bibliography only when every entry is already shown below. */
export function stripDuplicateSources(text: string, titles: string[]): string {
  if (!titles.length) return text;
  const normalize = (value: string) =>
    value
      .trim()
      .replace(NORMALIZE_BULLET_REGEX, '')
      .replace(NORMALIZE_DOC_REGEX, '')
      .replace(NORMALIZE_CLEAN_REGEX, '')
      .replace(NORMALIZE_TRAILING_PUNCT, '')
      .replace(NORMALIZE_SPACES_REGEX, ' ')
      .toLocaleLowerCase('ru')
      .replace(/ё/g, 'е');

  const known = new Set(titles.map(normalize));
  const lines = text.split('\n');
  let fence = '';
  let heading = -1;

  for (let index = 0; index < lines.length; index++) {
    const marker = lines[index].match(FENCE_REGEX);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = '';
      continue;
    }
    if (fence) continue;
    const label = lines[index]
      .trim()
      .replace(HEADING_STRIP_HASH, '')
      .replace(HEADING_STRIP_FORMAT, '')
      .replace(HEADING_STRIP_COLON, '')
      .toLocaleLowerCase('ru');
    if (KNOWN_HEADINGS.has(label)) heading = index;
  }
  if (heading < 0) return text;
  const entries = lines.slice(heading + 1).filter(line => line.trim());
  if (!entries.length || !entries.every(line => known.has(normalize(line)))) return text;
  return lines.slice(0, heading).join('\n').trimEnd();
}

export const ChatMessage: React.FC<ChatMessageProps> = memo(({ message }) => {
  const isUser = message.sender === 'user';
  const isSystem = message.sender === 'system';
  const userAttachments = isUser ? (message.attachments || []).filter(item => item.name && item.type !== 'knowledge_source') : [];
  const sourceEntries = !isUser ? (message.attachments || []).filter(item => item.type === 'knowledge_source' && item.name) : [];
  const sources = Array.from(new Map(sourceEntries.map(source => [source.data?.document_id || source.name, source])).values());
  const displayText = stripDuplicateSources(message.text, sources.map(source => source.name || ''));

  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<'like' | 'dislike' | null>(() => {
    try {
      return (localStorage.getItem(`feedback_${message.id}`) as 'like' | 'dislike') || null;
    } catch {
      return null;
    }
  });
  const [showReviewInput, setShowReviewInput] = useState(false);
  const [reviewComment, setReviewComment] = useState(() => {
    try {
      return localStorage.getItem(`feedback_comment_${message.id}`) || '';
    } catch {
      return '';
    }
  });
  const [reviewSaved, setReviewSaved] = useState(false);

  const handleCopyMessage = async () => {
    try {
      await navigator.clipboard.writeText(message.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = message.text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleFeedback = (type: 'like' | 'dislike') => {
    const next = feedback === type ? null : type;
    setFeedback(next);
    try {
      if (next) {
        localStorage.setItem(`feedback_${message.id}`, next);
      } else {
        localStorage.removeItem(`feedback_${message.id}`);
      }
    } catch {}
    if (next) setShowReviewInput(true);
  };

  const handleSaveReview = () => {
    try {
      if (reviewComment.trim()) {
        localStorage.setItem(`feedback_comment_${message.id}`, reviewComment.trim());
      }
    } catch {}
    setReviewSaved(true);
    setTimeout(() => {
      setReviewSaved(false);
      setShowReviewInput(false);
    }, 1500);
  };

  if (isSystem) {
    return (
      <div className="flex justify-center my-2 px-4">
        <div className="flex items-center space-x-2 px-3 py-1.5 rounded-full bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-900 text-amber-800 dark:text-amber-300 text-xs shadow-sm">
          <Info className="w-3.5 h-3.5 shrink-0" />
          <span>{message.text}</span>
        </div>
      </div>
    );
  }

  return (
    <div className={`flex w-full my-3 px-4 ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`flex max-w-[85%] sm:max-w-[75%] md:max-w-[70%] space-x-2.5 ${isUser ? 'flex-row-reverse space-x-reverse' : 'flex-row'}`}>
        {/* Avatar */}
        <div
          className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 shadow-sm text-white ${
            isUser
              ? 'bg-indigo-600'
              : 'bg-zinc-700'
          }`}
        >
          {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
        </div>

        {/* Message bubble */}
        <div className="flex flex-col">
          <div
            className={`px-4 py-2.5 rounded-2xl shadow-sm text-xs break-words ${
              isUser
                ? 'bg-indigo-600 text-white rounded-tr-none'
                : 'bg-white dark:bg-zinc-800 text-gray-900 dark:text-zinc-100 border border-gray-200/80 dark:border-zinc-700/80 rounded-tl-none'
            }`}
          >
            {/* Файловые вложения пользователя */}
            {userAttachments.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-2 pb-2 border-b border-indigo-400/40">
                {userAttachments.map((att, i) => (
                  <div key={i} className="inline-flex items-center gap-1.5 px-2 py-1 rounded bg-white/20 text-white text-[11px]">
                    <FileText size={12} className="shrink-0" />
                    <span className="truncate max-w-[150px] font-medium" title={att.name}>{att.name}</span>
                  </div>
                ))}
              </div>
            )}

            {isUser ? (
              <p className="whitespace-pre-wrap leading-relaxed">{message.text}</p>
            ) : (
              renderMarkdown(displayText)
            )}
          </div>

          {sources.length > 0 && (
            <div className="mt-2 text-xs text-gray-600 dark:text-zinc-400" aria-label="Предоставленные материалы">
              <p className="font-medium">Предоставленные материалы</p>
              <ul className="mt-1 space-y-1 list-disc pl-4">
                {sources.map((source, index) => {
                  const isSkill = (source.name || '').toLowerCase().includes('скилл') || (source.name || '').toLowerCase().includes('skill');
                  return (
                    <li className="break-words" key={source.data?.document_id || source.name || index}>
                      {isSkill ? (
                        <span className="inline-flex items-center gap-1 font-medium text-amber-600 dark:text-amber-400">
                          <Sparkles size={12} className="shrink-0" />
                          {source.name}
                        </span>
                      ) : (
                        source.name
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {/* Action bar for assistant message */}
          {!isUser && (
            <div className="flex items-center gap-2 mt-1.5 px-0.5 select-none">
              <button
                type="button"
                onClick={handleCopyMessage}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                title="Скопировать ответ в буфер обмена"
              >
                {copied ? <Check size={12} className="text-emerald-500" /> : <Copy size={12} />}
                <span>{copied ? 'Скопировано!' : 'Копировать'}</span>
              </button>

              <button
                type="button"
                onClick={() => setShowReviewInput(prev => !prev)}
                className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] transition-colors ${
                  feedback || reviewComment
                    ? 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/40 font-medium'
                    : 'text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-800'
                }`}
                title="Оставить отзыв"
              >
                <ThumbsUp size={12} className={feedback === 'like' ? 'text-emerald-500' : ''} />
                <span>{feedback === 'like' ? 'Полезно' : feedback === 'dislike' ? 'Замечание' : 'Отзыв'}</span>
              </button>

              {message.created_at && (
                <span className="text-[10px] text-zinc-400 dark:text-zinc-500 ml-auto">
                  {formatTime(message.created_at)}
                </span>
              )}
            </div>
          )}

          {/* Inline Review Form */}
          {!isUser && showReviewInput && (
            <div className="mt-1.5 p-2 rounded-lg bg-zinc-50 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700 text-xs shadow-sm space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="font-medium text-zinc-700 dark:text-zinc-200 text-[11px]">Оценка ответа:</span>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => handleFeedback('like')}
                    className={`p-1 rounded hover:bg-zinc-200 dark:hover:bg-zinc-700 transition ${
                      feedback === 'like' ? 'text-emerald-600 bg-emerald-100 dark:bg-emerald-950/60' : 'text-zinc-400'
                    }`}
                    title="Хороший ответ"
                  >
                    <ThumbsUp size={12} />
                  </button>
                  <button
                    type="button"
                    onClick={() => handleFeedback('dislike')}
                    className={`p-1 rounded hover:bg-zinc-200 dark:hover:bg-zinc-700 transition ${
                      feedback === 'dislike' ? 'text-rose-600 bg-rose-100 dark:bg-rose-950/60' : 'text-zinc-400'
                    }`}
                    title="Есть неточности или ошибки"
                  >
                    <ThumbsDown size={12} />
                  </button>
                </div>
              </div>
              <div className="flex gap-1.5">
                <input
                  type="text"
                  value={reviewComment}
                  onChange={e => setReviewComment(e.target.value)}
                  placeholder="Ваш комментарий или пожелание..."
                  className="flex-1 px-2 py-1 text-xs rounded border border-zinc-200 dark:border-zinc-600 bg-white dark:bg-zinc-900 text-zinc-800 dark:text-zinc-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
                <button
                  type="button"
                  onClick={handleSaveReview}
                  className="px-2 py-1 text-xs rounded bg-indigo-600 text-white font-medium hover:bg-indigo-700 transition shrink-0"
                >
                  {reviewSaved ? 'Сохранено!' : 'Отправить'}
                </button>
              </div>
            </div>
          )}

          {/* User timestamp */}
          {isUser && message.created_at && (
            <span className="text-[10px] text-gray-400 dark:text-zinc-500 mt-1 px-1 text-right">
              {formatTime(message.created_at)}
            </span>
          )}
        </div>
      </div>
    </div>
  );
});

ChatMessage.displayName = 'ChatMessage';
