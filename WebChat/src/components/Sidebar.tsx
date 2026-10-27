import React, { useMemo, useState, memo } from 'react';
import { Plus, Search, MessageSquare, Trash2, X } from 'lucide-react';
import type { Conversation } from '../types';

interface SidebarProps {
  conversations: Conversation[];
  currentId: string | null;
  onSelectConversation: (id: string) => void;
  onCreateConversation: () => void;
  onDeleteConversation: (id: string) => void | Promise<void>;
  isOpen: boolean;
  onClose?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = memo(({
  conversations,
  currentId,
  onSelectConversation,
  onCreateConversation,
  onDeleteConversation,
  isOpen,
  onClose,
}) => {
  const [search, setSearch] = useState('');
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter((c) => c.title.toLowerCase().includes(q));
  }, [conversations, search]);

  if (!isOpen) return null;

  return (
    <>
      <button className="sidebar-scrim" onClick={onClose} aria-label="Закрыть список диалогов" />
      <aside className="chat-sidebar" aria-label="Диалоги">
        <div className="sidebar-top">
          <h2>Диалоги</h2>
          <span>{conversations.length}</span>
          <button className="icon-button mobile-close" onClick={onClose} aria-label="Закрыть панель">
            <X size={18} />
          </button>
        </div>
        <button className="button button-primary new-chat" onClick={onCreateConversation}>
          <Plus size={18} aria-hidden="true" />
          Новый диалог
        </button>
        <label className="search-field sidebar-search">
          <Search size={16} aria-hidden="true" />
          <input
            aria-label="Поиск диалогов"
            placeholder="Поиск диалогов"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <div className="conversation-list">
          {filtered.length ? (
            filtered.map((conv) => (
              <div
                className={`conversation-row ${conv.id === currentId ? 'conversation-active' : ''}`}
                key={conv.id}
              >
                <button
                  className="conversation-select"
                  onClick={() => onSelectConversation(conv.id)}
                  aria-current={conv.id === currentId ? 'page' : undefined}
                >
                  <MessageSquare size={16} aria-hidden="true" />
                  <span>{conv.title || 'Новый диалог'}</span>
                </button>
                <button
                  className="icon-button conversation-delete"
                  onClick={() => {
                    setDeletingId(conv.id);
                    setError('');
                  }}
                  aria-label={`Удалить диалог ${conv.title}`}
                >
                  <Trash2 size={15} />
                </button>
                {deletingId === conv.id && (
                  <div className="delete-confirm">
                    <p>Удалить диалог и его сообщения?</p>
                    {error && <p role="alert">{error}</p>}
                    <div>
                      <button
                        className="text-button danger"
                        disabled={busy}
                        onClick={async () => {
                          setBusy(true);
                          try {
                            await onDeleteConversation(conv.id);
                            setDeletingId(null);
                          } catch {
                            setError('Не удалось удалить. Повторите запрос.');
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        {busy ? 'Удаляем…' : 'Удалить'}
                      </button>
                      <button
                        className="text-button"
                        disabled={busy}
                        onClick={() => setDeletingId(null)}
                      >
                        Отмена
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))
          ) : (
            <div className="sidebar-empty">
              <MessageSquare size={24} aria-hidden="true" />
              <p>{search ? 'Диалоги не найдены' : 'Здесь появятся ваши диалоги'}</p>
              <small>{search ? 'Попробуйте другой запрос.' : 'Начните с вопроса ассистенту.'}</small>
            </div>
          )}
        </div>
        <div className="sidebar-footer">
          <span className="brand-mark small" aria-hidden="true">V</span>
          <span>VIIO Ассистент<small>Знания вашей команды</small></span>
        </div>
      </aside>
    </>
  );
});

Sidebar.displayName = 'Sidebar';
