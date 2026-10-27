import React, { useState } from 'react';
import { User, Bot, Copy, Check, Info } from 'lucide-react';
import type { Message } from '../types';

interface ChatMessageProps {
  message: Message;
}

const CodeBlock: React.FC<{ raw: string }> = ({ raw }) => {
  const [copied, setCopied] = useState(false);

  // First line might be language
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
};

function renderInline(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  const regex = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith('`') && token.endsWith('`')) {
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

function renderMarkdown(content: string): React.ReactNode {
  // 1. Split by code blocks ```
  const codeBlockRegex = /```([\s\S]*?)```/g;
  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = codeBlockRegex.exec(content)) !== null) {
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

function renderTextLines(section: string, keyPrefix: string): React.ReactNode {
  const lines = section.split('\n');
  return (
    <div key={keyPrefix} className="space-y-1">
      {lines.map((line, idx) => {
        const trimmed = line.trim();
        if (!trimmed) {
          return <div key={idx} className="h-1.5" />;
        }
        if (line.startsWith('### ')) {
          return (
            <h3 key={idx} className="text-xs font-bold text-gray-900 dark:text-zinc-100 mt-2 mb-1">
              {renderInline(line.slice(4))}
            </h3>
          );
        }
        if (line.startsWith('## ')) {
          return (
            <h2 key={idx} className="text-sm font-bold text-gray-900 dark:text-zinc-100 mt-2.5 mb-1 border-b border-gray-200 dark:border-zinc-800 pb-0.5">
              {renderInline(line.slice(3))}
            </h2>
          );
        }
        if (line.startsWith('# ')) {
          return (
            <h1 key={idx} className="text-base font-extrabold text-gray-900 dark:text-zinc-100 mt-3 mb-1.5 border-b border-gray-200 dark:border-zinc-800 pb-1">
              {renderInline(line.slice(2))}
            </h1>
          );
        }
        if (line.startsWith('> ')) {
          return (
            <blockquote
              key={idx}
              className="border-l-2 border-indigo-500 pl-2.5 italic text-gray-600 dark:text-zinc-400 my-1 text-xs"
            >
              {renderInline(line.slice(2))}
            </blockquote>
          );
        }
        if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
          return (
            <div key={idx} className="flex items-start space-x-1.5 ml-2 text-xs">
              <span className="text-indigo-500 shrink-0 select-none">•</span>
              <span>{renderInline(trimmed.slice(2))}</span>
            </div>
          );
        }
        const orderedMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
        if (orderedMatch) {
          return (
            <div key={idx} className="flex items-start space-x-1.5 ml-2 text-xs">
              <span className="font-semibold text-indigo-500 shrink-0 select-none">{orderedMatch[1]}.</span>
              <span>{renderInline(orderedMatch[2])}</span>
            </div>
          );
        }

        return (
          <p key={idx} className="text-xs leading-relaxed">
            {renderInline(line)}
          </p>
        );
      })}
    </div>
  );
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

export const ChatMessage: React.FC<ChatMessageProps> = ({ message }) => {
  const isUser = message.sender === 'user';
  const isSystem = message.sender === 'system';
  const sources = !isUser ? (message.attachments || []).filter(item => item.type === 'knowledge_source' && item.name) : [];

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
            {isUser ? (
              <p className="whitespace-pre-wrap leading-relaxed">{message.text}</p>
            ) : (
              renderMarkdown(message.text)
            )}
          </div>

          {sources.length > 0 && (
            <div className="mt-2 text-xs text-gray-600 dark:text-zinc-400" aria-label="Предоставленные материалы">
              <p className="font-medium">Предоставленные материалы</p>
              <ul className="mt-1 space-y-1 list-disc pl-4">
                {sources.map((source, index) => <li className="break-words" key={index}>{source.name}</li>)}
              </ul>
            </div>
          )}

          {/* Timestamp */}
          {message.created_at && (
            <span
              className={`text-[10px] text-gray-400 dark:text-zinc-500 mt-1 px-1 ${
                isUser ? 'text-right' : 'text-left'
              }`}
            >
              {formatTime(message.created_at)}
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
