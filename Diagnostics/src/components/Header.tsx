import { memo } from 'react';
import { Sun, Moon, Activity } from 'lucide-react';

interface HeaderProps {
  dark: boolean;
  onToggleTheme: () => void;
}

export const Header = memo(function Header({ dark, onToggleTheme }: HeaderProps) {
  return (
    <header className="app-header">
      <div className="brand-group">
        <div className="brand">
          VIIO <span>Диагностика</span>
        </div>
        <div className="local-badge" title="Сайт опубликован только на локальном интерфейсе хоста">
          <Activity size={14} className="badge-icon" aria-hidden="true" />
          <span>127.0.0.1:3010</span>
        </div>
      </div>
      <div className="header-actions">
        <button
          className="theme-toggle"
          onClick={onToggleTheme}
          aria-label={dark ? 'Переключить на светлую тему' : 'Переключить на тёмную тему'}
          title={dark ? 'Светлая тема' : 'Тёмная тема'}
        >
          {dark ? <Sun size={17} aria-hidden="true" /> : <Moon size={17} aria-hidden="true" />}
        </button>
      </div>
    </header>
  );
});
