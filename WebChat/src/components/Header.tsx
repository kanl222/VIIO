import React, { useEffect, useState, memo } from 'react';
import { Sun, Moon, LogOut, PanelLeft, ShieldCheck, MessageSquare } from 'lucide-react';
import type { User } from '../types';

interface HeaderProps {
  title?: string;
  user: User | null;
  onLogout: () => void;
  onToggleSidebar?: () => void;
  view?: 'chat' | 'admin';
  onNavigate?: (view: 'chat' | 'admin') => void;
}

export const Header: React.FC<HeaderProps> = memo(({
  user,
  onLogout,
  onToggleSidebar,
  view = 'chat',
  onNavigate,
}) => {
  const [isDark, setIsDark] = useState(() => {
    try {
      const saved = localStorage.getItem('viio-theme');
      if (saved) return saved === 'dark';
    } catch {}
    return typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches;
  });

  useEffect(() => {
    document.documentElement.classList.toggle('dark', isDark);
    try {
      localStorage.setItem('viio-theme', isDark ? 'dark' : 'light');
    } catch {}
  }, [isDark]);

  return (
    <header className="app-header">
      <div className="header-brand">
        {view === 'chat' && (
          <button className="icon-button" onClick={onToggleSidebar} aria-label="Боковая панель">
            <PanelLeft size={19} />
          </button>
        )}
        <span className="brand-mark" aria-hidden="true">V</span>
        <span className="brand-name">VIIO</span>
        <span className="brand-divider" />
        <span className="workspace-name">Рабочее пространство</span>
      </div>
      <nav className="workspace-switcher" aria-label="Рабочее пространство">
        <button
          aria-current={view === 'chat' ? 'page' : undefined}
          onClick={() => onNavigate?.('chat')}
        >
          <MessageSquare size={16} aria-hidden="true" />
          <span>Ассистент</span>
        </button>
        {user?.role === 'admin' && (
          <button
            aria-current={view === 'admin' ? 'page' : undefined}
            onClick={() => onNavigate?.('admin')}
          >
            <ShieldCheck size={16} aria-hidden="true" />
            <span>Администрирование</span>
          </button>
        )}
      </nav>
      <div className="header-actions">
        <button
          className="icon-button"
          onClick={() => setIsDark((v) => !v)}
          aria-label={isDark ? 'Включить светлую тему' : 'Включить тёмную тему'}
        >
          {isDark ? <Sun size={18} /> : <Moon size={18} />}
        </button>
        {user && (
          <>
            <span className="header-username" title={user.username}>
              {user.username}
            </span>
            <button className="icon-button" onClick={onLogout} aria-label="Выйти из аккаунта">
              <LogOut size={18} />
            </button>
          </>
        )}
      </div>
    </header>
  );
});

Header.displayName = 'Header';
