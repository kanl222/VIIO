import React, { useState } from 'react';
import { useAuth } from './hooks/useAuth';
import { useConversations } from './hooks/useConversations';
import { useChat } from './hooks/useChat';
import { Header } from './components/Header';
import { Sidebar } from './components/Sidebar';
import { ChatView } from './components/ChatView';
import { ChatInput } from './components/ChatInput';
import { AuthModal } from './components/AuthModal';
import { Loader2 } from 'lucide-react';
import { AdminView as StaticAdminView } from './components/AdminView';

// Безопасный фолбэк хуков для легковесных тестовых окружений vm (например, раннера chat-regressions)
const useCallback: <T extends (...args: any[]) => any>(callback: T, deps: any[]) => T =
  React.useCallback ?? ((fn: any) => fn);
const useMemo: <T>(factory: () => T, deps: any[]) => T =
  React.useMemo ?? ((fn: any) => fn());
const Suspense: React.FC<{ fallback?: React.ReactNode; children?: React.ReactNode }> =
  React.Suspense ?? (({ children }) => <>{children}</>);

// Динамический импорт панели администратора для ускорения начальной загрузки чата (vercel-react-best-practices)
const LazyAdminView = typeof React.lazy === 'function'
  ? React.lazy(() => import('./components/AdminView').then((m) => ({ default: m.AdminView })))
  : StaticAdminView;

export const App: React.FC = () => {
  const { user, loading: authLoading, login, register, logout, checkAuth } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(() => typeof window !== 'undefined' && window.innerWidth >= 768);
  const [view, setView] = useState<'chat' | 'admin'>('chat');

  const {
    conversations,
    currentId,
    loading: convsLoading,
    selectConversation,
    createConversation,
    deleteConversation,
    reloadConversations,
    error: conversationError,
  } = useConversations(!!user);

  const currentConv = useMemo(
    () => conversations.find((c) => c.id === currentId),
    [conversations, currentId]
  );

  const {
    messages,
    loading: chatLoading,
    submitting,
    plan,
    progressText,
    sendMessage,
    error: chatError,
  } = useChat(currentId, reloadConversations);

  const handleCreateNewChat = useCallback(async () => {
    try {
      await createConversation('Новый диалог');
      if (typeof window !== 'undefined' && window.innerWidth < 768) {
        setSidebarOpen(false);
      }
    } catch {
      // обрабатывается в хуке
    }
  }, [createConversation]);

  const handleSendMessage = useCallback(
    async (text: string, options?: import('./api/chat').RagOptions) => {
      if (!currentId) {
        try {
          const title = text.slice(0, 30) + (text.length > 30 ? '...' : '');
          const newConv = await createConversation(title);
          if (newConv) {
            await sendMessage(text, newConv.id, options);
          }
        } catch {
          // обрабатывается в хуке
        }
      } else {
        await sendMessage(text, currentId, options);
      }
    },
    [currentId, createConversation, sendMessage]
  );

  const handleQuickPrompt = useCallback(
    (prompt: string) => {
      void handleSendMessage(prompt);
    },
    [handleSendMessage]
  );

  const handleSelectConversation = useCallback(
    (id: string) => {
      selectConversation(id);
      if (typeof window !== 'undefined' && window.innerWidth < 768) {
        setSidebarOpen(false);
      }
    },
    [selectConversation]
  );

  const handleLogout = useCallback(() => {
    setView('chat');
    void logout();
  }, [logout]);

  const handleToggleSidebar = useCallback(() => {
    setSidebarOpen((prev) => !prev);
  }, []);

  const handleCloseSidebar = useCallback(() => {
    setSidebarOpen(false);
  }, []);

  if (authLoading) {
    return (
      <div className="h-screen w-screen flex flex-col items-center justify-center bg-gray-50 dark:bg-zinc-950 text-gray-500 dark:text-zinc-400">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-2" />
        <span className="text-xs font-medium">Загрузка VIIO WebChat...</span>
      </div>
    );
  }

  const isHistoryLoading = chatLoading || (convsLoading && !currentId && conversations.length > 0);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Перейти к содержимому</a>

      {/* Header */}
      <Header
        title={currentConv?.title}
        user={user}
        onLogout={handleLogout}
        onToggleSidebar={handleToggleSidebar}
        view={view}
        onNavigate={setView}
      />

      {/* Main Content Area */}
      {view === 'admin' && user?.role === 'admin' ? (
        <Suspense fallback={
          <div className="flex-1 flex flex-col items-center justify-center p-8 text-gray-500 dark:text-zinc-400">
            <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-2" />
            <span className="text-xs font-medium">Загрузка панели администратора...</span>
          </div>
        }>
          <LazyAdminView user={user} onAccessChanged={checkAuth} />
        </Suspense>
      ) : (
        <div className="flex-1 flex overflow-hidden relative">
          <Sidebar
            conversations={conversations}
            currentId={currentId}
            onSelectConversation={handleSelectConversation}
            onCreateConversation={handleCreateNewChat}
            onDeleteConversation={deleteConversation}
            isOpen={sidebarOpen}
            onClose={handleCloseSidebar}
          />

          <main id="main-content" className="chat-main">
            {(conversationError || chatError) && (
              <div className="notice notice-error chat-notice" role="alert">
                {conversationError || chatError}
              </div>
            )}
            <ChatView
              messages={messages}
              loading={isHistoryLoading}
              submitting={submitting}
              plan={plan}
              progressText={progressText}
              conversationTitle={currentConv?.title}
              onQuickPrompt={handleQuickPrompt}
            />

            <ChatInput
              onSendMessage={handleSendMessage}
              disabled={submitting || !user}
              placeholder={
                !user
                  ? 'Войдите для отправки сообщений...'
                  : 'Напишите сообщение... (Enter для отправки)'
              }
            />
          </main>
        </div>
      )}

      {/* Authentication Modal */}
      <AuthModal
        isOpen={!user}
        onLogin={login}
        onRegister={register}
      />
    </div>
  );
};

export default App;
