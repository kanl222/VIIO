import React, { useEffect, useRef, useState, memo, type FormEvent } from 'react';
import { ArrowRight, Loader2, ShieldCheck } from 'lucide-react';

interface AuthModalProps {
  isOpen: boolean;
  onLogin: (username: string, password: string) => Promise<unknown>;
  onRegister: (username: string, password: string) => Promise<unknown>;
}

export const AuthModal: React.FC<AuthModalProps> = memo(({ isOpen, onLogin, onRegister }) => {
  const [tab, setTab] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const formRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) {
      setPassword('');
      return;
    }
    const panel = formRef.current;
    const previous = document.activeElement as HTMLElement | null;
    panel?.querySelector<HTMLInputElement>('input')?.focus();

    const trap = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || !panel) return;
      const controls = Array.from(
        panel.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), a[href]')
      );
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };

    document.addEventListener('keydown', trap);
    return () => {
      document.removeEventListener('keydown', trap);
      previous?.focus();
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    if (username.trim().length < 3 || username.trim().length > 64 || password.length < 6) {
      setError('Введите логин от 3 до 64 символов и пароль не короче 6 символов.');
      return;
    }
    setLoading(true);
    try {
      await (tab === 'login' ? onLogin : onRegister)(username.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось войти. Повторите попытку.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="auth-screen"
      role="dialog"
      aria-modal="true"
      aria-labelledby="auth-heading"
      ref={formRef}
    >
      <div className="auth-panel">
        <div className="auth-brand">
          <span className="brand-mark" aria-hidden="true">V</span>
          <span className="brand-name">VIIO</span>
        </div>
        <h1 id="auth-heading">{tab === 'login' ? 'Ваше рабочее пространство' : 'Присоединяйтесь к VIIO'}</h1>
        <p>
          {tab === 'login'
            ? 'Войдите, чтобы работать с ассистентом и продолжить свои диалоги.'
            : 'Создайте учётную запись для работы со знаниями вашей команды.'}
        </p>
        <div className="auth-tabs" role="tablist" aria-label="Вход или регистрация">
          {(['login', 'register'] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              disabled={loading}
              onClick={() => {
                setTab(value);
                setError('');
              }}
            >
              {value === 'login' ? 'Вход' : 'Регистрация'}
            </button>
          ))}
        </div>
        {error && <div className="notice notice-error" role="alert">{error}</div>}
        <form onSubmit={submit}>
          <label className="field" htmlFor="auth-username">
            Имя пользователя
            <input
              id="auth-username"
              name="username"
              autoComplete="username"
              required
              minLength={3}
              maxLength={64}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Ваш логин"
              disabled={loading}
            />
          </label>
          <label className="field" htmlFor="auth-password">
            Пароль
            <input
              id="auth-password"
              name="password"
              type="password"
              autoComplete={tab === 'login' ? 'current-password' : 'new-password'}
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Не менее 6 символов"
              disabled={loading}
            />
          </label>
          <button className="button button-primary" type="submit" disabled={loading}>
            {loading ? (
              <Loader2 size={18} className="animate-spin" />
            ) : (
              <ArrowRight size={18} aria-hidden="true" />
            )}
            {loading ? 'Подождите…' : tab === 'login' ? 'Войти в пространство' : 'Создать учётную запись'}
          </button>
        </form>
        <div className="auth-note">
          <ShieldCheck size={16} className="inline mr-1" aria-hidden="true" />
          Доступ к базе знаний зависит от вашей роли и подразделения.
        </div>
      </div>
    </div>
  );
});

AuthModal.displayName = 'AuthModal';
