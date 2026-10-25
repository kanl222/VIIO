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
import { AdminView } from './components/AdminView';

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

  const currentConv = conversations.find((c) => c.id === currentId);

  const {
    messages,
    loading: chatLoading,
    submitting,
    sendMessage,
    error: chatError,
  } = useChat(currentId, reloadConversations);

  const handleCreateNewChat = async () => {
    try {
      await createConversation('Новый диалог');
      if (window.innerWidth < 768) setSidebarOpen(false);
    } catch {
      // handled in hook
    }
  };

  const handleSendMessage = async (text: string) => {
    if (!currentId) {
      try {
        const title = text.slice(0, 30) + (text.length > 30 ? '...' : '');
        const newConv = await createConversation(title);
        if (newConv) {
          await sendMessage(text, newConv.id);
        }
      } catch {
        // handled in hook
      }
    } else {
      await sendMessage(text);
    }
  };

  const handleQuickPrompt = (prompt: string) => {
    handleSendMessage(prompt);
  };

  if (authLoading) {
    return (
      <div className="h-screen w-screen flex flex-col items-center justify-center bg-gray-50 dark:bg-zinc-950 text-gray-500 dark:text-zinc-400">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-500 mb-2" />
        <span className="text-xs font-medium">Загрузка VIIO WebChat...</span>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Перейти к содержимому</a>
      {/* Header */}
      <Header
        title={currentConv?.title}
        user={user}
        onLogout={() => { setView('chat'); void logout(); }}
        onToggleSidebar={() => setSidebarOpen((prev) => !prev)}
        view={view}
        onNavigate={setView}
      />

      {/* Main Content Area */}
      {view === 'admin' && user?.role === 'admin' ? <AdminView user={user} onAccessChanged={checkAuth} /> : <div className="flex-1 flex overflow-hidden relative">
        <Sidebar
          conversations={conversations}
          currentId={currentId}
          onSelectConversation={(id) => { selectConversation(id); if (window.innerWidth < 768) setSidebarOpen(false); }}
          onCreateConversation={handleCreateNewChat}
          onDeleteConversation={deleteConversation}
          isOpen={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
        />

        <main id="main-content" className="chat-main">
          {(conversationError || chatError) && <div className="notice notice-error chat-notice" role="alert">{conversationError || chatError}</div>}
          <ChatView
            messages={messages}
            loading={chatLoading || (convsLoading && !currentId && conversations.length > 0)}
            submitting={submitting}
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
      </div>}

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
