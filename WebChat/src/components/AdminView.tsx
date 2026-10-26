import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Activity, ArrowLeft, ArrowRight, BookOpen, Check, ChevronRight, FileText, LayoutDashboard, Loader2, RefreshCw, Save, Search, ShieldCheck, Upload, Users, X } from 'lucide-react';
import * as adminApi from '../api/admin';
import type { AdminUser, Overview } from '../api/admin';
import type { User } from '../types';

type Section = 'overview' | 'users' | 'knowledge';
export const roleNames: Record<string, string> = { admin: 'Администратор', manager: 'Руководитель', programmer: 'Программист', employee: 'Сотрудник' };
const errorText = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить действие. Попробуйте ещё раз.';
const date = (value: string) => new Date(value).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });

function Notice({ children, success = false }: { children: React.ReactNode; success?: boolean }) {
  return <div className={`notice ${success ? 'notice-success' : 'notice-error'}`} role={success ? 'status' : 'alert'}>{children}</div>;
}

function OverviewPanel({ onNavigate }: { onNavigate: (section: Section) => void }) {
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true); setError('');
    try { setData(await adminApi.getOverview(controller.signal)); }
    catch (err) { if (!controller.signal.aborted) setError(errorText(err)); }
    finally { if (!controller.signal.aborted) setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); return () => request.current?.abort(); }, [refresh]);
  return <>
    <div className="page-heading"><div><h1>Обзор системы</h1><p>Пользователи, знания и состояние рабочего пространства.</p></div>
      <button className="button button-secondary" onClick={refresh} disabled={loading}><RefreshCw size={16} className={loading ? 'animate-spin' : ''} aria-hidden="true" />Обновить</button>
    </div>
    {error && <Notice>{error}</Notice>}
    {loading && !data ? <div className="loading-state" role="status"><Loader2 className="animate-spin" size={22} />Загружаем состояние системы…</div> : data && <>
      <dl className="workspace-stats">{[
        ['Пользователи', data.counts.users], ['Диалоги', data.counts.conversations],
        ['Сообщения', data.counts.messages], ['Активные сессии', data.counts.sessions],
      ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{Number(value).toLocaleString('ru-RU')}</dd></div>)}</dl>
      <div className="overview-layout">
        <section className="surface-section"><div className="section-heading"><h2>Состояние сервисов</h2><Activity size={18} aria-hidden="true" /></div>
          <div className="service-list">{[
            { title: 'База пользователей', text: 'Учётные записи, диалоги и история', ok: data.database },
            { title: 'Диалоговый агент', text: data.agent.model || 'Нет подключения к сервису', ok: data.agent.healthy },
            { title: 'База знаний', text: data.agent.chunks !== undefined ? `${data.agent.chunks.toLocaleString('ru-RU')} фрагментов документов` : 'Состояние не получено', ok: data.agent.healthy },
          ].map(service => <div className="service-row" key={service.title}><div><strong>{service.title}</strong><p>{service.text}</p></div>
            <span className={`status-badge ${service.ok ? 'status-good' : 'status-bad'}`}><span className="status-dot" />{service.ok ? 'Доступен' : 'Недоступен'}</span></div>)}</div>
          <p className="section-note">Проверено {new Date(data.checked_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}. Состояние обновляется по запросу.</p>
        </section>
        <section className="surface-section"><h2>Управление пространством</h2>
          <button className="action-row" onClick={() => onNavigate('users')}><Users size={20} aria-hidden="true" /><span><strong>Пользователи и доступ</strong><small>Роли и подразделения команды</small></span><ChevronRight size={17} aria-hidden="true" /></button>
          <button className="action-row" onClick={() => onNavigate('knowledge')}><BookOpen size={20} aria-hidden="true" /><span><strong>Пополнить базу знаний</strong><small>Документы, на которые опирается ассистент</small></span><ChevronRight size={17} aria-hidden="true" /></button>
          <div className="admin-guidance"><ShieldCheck size={20} aria-hidden="true" /><p>Новые пользователи получают роль сотрудника. Расширенные права назначаются здесь.</p></div>
        </section>
      </div>
    </>}
  </>;
}

function UserEditor({ user, currentUser, onClose, onSaved }: {
  user: AdminUser; currentUser: User; onClose: () => void; onSaved: (user: AdminUser) => void;
}) {
  const [role, setRole] = useState(user.role || 'employee');
  const [departments, setDepartments] = useState((user.departments || []).join(', '));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const save = async (event: FormEvent) => {
    event.preventDefault(); setSaving(true); setError('');
    try {
      const result = await adminApi.updateUser(user.id, role, departments.split(',').map(d => d.trim()).filter(Boolean));
      onSaved(result.user);
    } catch (err) { setError(errorText(err)); }
    finally { setSaving(false); }
  };
  return <section className="user-editor" aria-labelledby="edit-user-title">
    <div className="section-heading"><div><h2 id="edit-user-title">Доступ: {user.username}</h2><p>{user.id === currentUser.id ? 'Вы редактируете свою учётную запись.' : 'Изменения применяются со следующего запроса пользователя.'}</p></div><button className="icon-button" aria-label="Закрыть редактирование" onClick={onClose} disabled={saving}><X size={18} /></button></div>
    <form onSubmit={save}>
      <div className="form-grid"><label className="field">Роль<select value={role} onChange={e => setRole(e.target.value)} disabled={saving} autoFocus>
        {!roleNames[role] && <option value={role}>{role}</option>}
        {Object.entries(roleNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <label className="field">Подразделения<input value={departments} onChange={e => setDepartments(e.target.value)} disabled={saving} placeholder="general, finance" maxLength={1950} /><small>Через запятую. Пустое поле убирает доступ по подразделениям.</small></label></div>
      {role === 'admin' && <p className="field-help">Администратор видит все документы и может изменять права пользователей.</p>}
      {role === 'programmer' && <p className="field-help">Доступ к coder для написания кода в чат и к документам сотрудников. Coder получает найденные сведения с учётом прав пользователя.</p>}
      {error && <Notice>{error}</Notice>}
      <div className="form-actions"><button className="button button-primary" type="submit" disabled={saving}>{saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}Сохранить права</button><button className="button button-secondary" type="button" onClick={onClose} disabled={saving}>Отмена</button></div>
    </form>
  </section>;
}

function UsersPanel({ currentUser, onAccessChanged }: { currentUser: User; onAccessChanged: () => void }) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ users: AdminUser[]; total: number } | null>(null);
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    adminApi.getUsers(filter, page, controller.signal).then(setData)
      .catch(err => { if (!controller.signal.aborted) setError(errorText(err)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filter, page, revision]);
  const saved = (user: AdminUser) => {
    setSelected(null); setNotice(`Права пользователя ${user.username} сохранены.`); setRevision(n => n + 1);
    if (user.id === currentUser.id) onAccessChanged();
  };
  return <>
    <div className="page-heading"><div><h1>Пользователи</h1><p>Управляйте ролями и доступом к знаниям компании.</p></div><span className="count-label">{data ? `${data.total} всего` : 'Загрузка…'}</span></div>
    {notice && <Notice success>{notice}</Notice>}
    <form className="table-toolbar" onSubmit={e => { e.preventDefault(); setFilter(query.trim()); setPage(1); setSelected(null); setRevision(n => n + 1); }}>
      <label className="search-field"><Search size={17} aria-hidden="true" /><input aria-label="Поиск пользователя по имени" placeholder="Найти пользователя по имени" value={query} onChange={e => setQuery(e.target.value)} maxLength={64} /></label>
      <button className="button button-secondary" type="submit" disabled={loading}>Найти</button>
      <button className="icon-button" type="button" onClick={() => setRevision(n => n + 1)} disabled={loading} aria-label="Обновить пользователей"><RefreshCw size={17} className={loading ? 'animate-spin' : ''} /></button>
    </form>
    {error ? <Notice>{error} <button className="text-button" onClick={() => setRevision(n => n + 1)}>Повторить</button></Notice> :
      <div className="table-container" aria-busy={loading}>
        {loading ? <div className="loading-state" role="status"><Loader2 size={20} className="animate-spin" />Загружаем пользователей…</div> : data?.users.length ? <table className="user-table"><caption className="sr-only">Пользователи рабочего пространства и их права доступа</caption>
          <thead><tr><th scope="col">Пользователь</th><th scope="col">Роль</th><th scope="col">Подразделения</th><th scope="col">Регистрация</th><th scope="col"><span className="sr-only">Действия</span></th></tr></thead>
          <tbody>{data.users.map(user => <tr key={user.id} className={selected?.id === user.id ? 'selected-row' : ''}>
            <td><div className="table-user"><span className="user-avatar" aria-hidden="true">{user.username.slice(0, 1).toUpperCase()}</span><span><strong>{user.username}</strong>{user.id === currentUser.id && <small>Это вы</small>}</span></div></td>
            <td><span className={`role-badge ${user.role === 'admin' ? 'role-admin' : ''}`}>{roleNames[user.role || 'employee'] || user.role}</span></td>
            <td><span className="department-text">{user.departments?.join(', ') || 'Не назначены'}</span></td><td className="date-cell">{date(user.created_at)}</td>
            <td><button className="text-button" onClick={() => { setSelected(user); setNotice(''); }} aria-label={`Изменить права ${user.username}`}>Изменить<ChevronRight size={14} aria-hidden="true" /></button></td>
          </tr>)}</tbody></table> : <div className="empty-state"><Users size={28} aria-hidden="true" /><h2>Пользователи не найдены</h2><p>Попробуйте другое имя или очистите поиск.</p><button className="button button-secondary" onClick={() => { setQuery(''); setFilter(''); setPage(1); }}>Сбросить поиск</button></div>}
      </div>}
    {!!data?.total && <div className="pagination"><span>Страница {page} из {Math.max(1, Math.ceil(data.total / 20))}</span><div><button className="icon-button" disabled={page <= 1 || loading} onClick={() => { setPage(p => p - 1); setSelected(null); }} aria-label="Предыдущая страница"><ArrowLeft size={17} /></button><button className="icon-button" disabled={page * 20 >= data.total || loading} onClick={() => { setPage(p => p + 1); setSelected(null); }} aria-label="Следующая страница"><ArrowRight size={17} /></button></div></div>}
    {selected && <UserEditor key={selected.id} user={selected} currentUser={currentUser} onClose={() => setSelected(null)} onSaved={saved} />}
  </>;
}

function KnowledgePanel() {
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [access, setAccess] = useState('employees');
  const [departments, setDepartments] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [extracting, setExtracting] = useState(false);
  const [extractionInfo, setExtractionInfo] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (extracting) return;
    setSaving(true); setError(''); setSuccess('');
    try {
      const result = await adminApi.addDocument({ title, content, is_public: access === 'public',
        allowed_roles: access === 'employees' ? ['employee', 'manager'] : [],
        allowed_departments: access === 'departments' ? departments.split(',').map(d => d.trim()).filter(Boolean) : [],
      });
      setSuccess(`«${result.title}» добавлен в базу знаний. Создано фрагментов: ${result.chunks_count}.`);
      setTitle(''); setContent('');
      setExtractionInfo('');
      if (fileInput.current) fileInput.current.value = '';
    } catch (err) { setError(errorText(err)); }
    finally { setSaving(false); }
  };
  const readFile = async (file?: File) => {
    if (!file) return;
    setError(''); setSuccess(''); setExtractionInfo('');
    const pdf = /\.pdf$/i.test(file.name);
    if (!/\.(txt|md|markdown|pdf)$/i.test(file.name) || file.size > (pdf ? 10 : 2) * 1024 * 1024) {
      setError('Выберите PDF до 10 МБ или TXT/Markdown до 2 МБ.'); return;
    }
    setExtracting(true);
    try {
      const extracted = pdf ? await adminApi.extractPdf(file) : null;
      const text = extracted ? extracted.content : await file.text();
      if (text.length > 500000) { setError('В документе больше 500 000 символов. Разделите его на части.'); return; }
      setContent(text); if (!title) setTitle(file.name.replace(/\.[^.]+$/, '').slice(0, 255));
      if (extracted) setExtractionInfo(`Из PDF получен текст: ${extracted.pages} стр. Проверьте его перед добавлением в базу знаний.`);
    } catch (err) { setError(errorText(err)); }
    finally { setExtracting(false); }
  };
  return <>
    <div className="page-heading"><div><h1>База знаний</h1><p>Добавляйте материалы, чтобы ассистент отвечал на основе документов компании.</p></div></div>
    <div className="knowledge-layout"><form className="document-form" onSubmit={submit} aria-busy={saving || extracting}>
      <div className="section-heading"><h2>Новый документ</h2><FileText size={19} aria-hidden="true" /></div>
      {error && <Notice>{error}</Notice>}{success && <Notice success><Check size={18} aria-hidden="true" />{success}</Notice>}
      <label className="field">Название<input required value={title} onChange={e => setTitle(e.target.value)} placeholder="Например, регламент работы с клиентами" maxLength={255} disabled={saving || extracting} /></label>
      <label className="file-picker"><Upload size={19} aria-hidden="true" /><span><strong>Выбрать файл</strong><small>PDF до 10 МБ · TXT или Markdown до 2 МБ</small></span><input ref={fileInput} type="file" accept=".pdf,.txt,.md,.markdown,application/pdf,text/plain,text/markdown" disabled={saving || extracting} onChange={e => void readFile(e.target.files?.[0])} aria-label="Выбрать PDF или текстовый файл" /></label>
      {extracting && <p className="field-help" role="status"><Loader2 size={16} className="animate-spin" /> Извлекаем текст. Для сканов обработка может занять несколько минут.</p>}
      {extractionInfo && <p className="field-help" role="status">{extractionInfo}</p>}
      <label className="field">Содержание<textarea required value={content} onChange={e => setContent(e.target.value)} placeholder="Вставьте текст документа…" rows={10} maxLength={500000} disabled={saving || extracting} /><small className="character-count">{content.length.toLocaleString('ru-RU')} / 500 000 символов</small></label>
      <fieldset disabled={saving || extracting}><legend>Кому доступен документ</legend><div className="access-options">{[
        ['employees', 'Сотрудникам', 'Сотрудники, руководители и программисты'], ['departments', 'Подразделениям', 'Только выбранные отделы'], ['private', 'Администраторам', 'Ограниченный доступ'], ['public', 'Всем', 'Включая гостевые запросы'],
      ].map(([value, label, hint]) => <label key={value} className={`access-option ${access === value ? 'access-selected' : ''}`}><input type="radio" name="document-access" checked={access === value} onChange={() => setAccess(value)} /><span><strong>{label}</strong><small>{hint}</small></span></label>)}</div></fieldset>
      {access === 'departments' && <label className="field">Подразделения<input required value={departments} onChange={e => setDepartments(e.target.value)} placeholder="finance, it" disabled={saving || extracting} maxLength={1950} /><small>Названия через запятую, как в профилях пользователей.</small></label>}
      <div className="form-actions"><button className="button button-primary" type="submit" disabled={saving || extracting || !title.trim() || !content.trim()}>{saving ? <Loader2 className="animate-spin" size={17} /> : <Upload size={17} />}{saving ? 'Добавляем документ…' : 'Добавить в базу знаний'}</button></div>
    </form><aside className="document-help"><BookOpen size={25} aria-hidden="true" /><h2>Знания для точных ответов</h2><p>Добавляйте регламенты, инструкции и ответы на частые вопросы. Текст будет разбит на фрагменты для поиска.</p><h3>Проверьте перед загрузкой</h3><ul><li>У документа понятное название.</li><li>Содержание актуально и не дублирует другие материалы.</li><li>Права соответствуют содержимому.</li></ul><p>Администраторы и автор документа сохраняют доступ при любых настройках. Права на роль и подразделение действуют независимо: достаточно одного совпадения.</p><p className="section-note">PDF: до 10 МБ и 50 страниц. Сначала проверьте извлечённый текст, затем задайте доступ и добавьте документ. DOCX пока не поддерживается.</p></aside></div>
  </>;
}

export function AdminView({ user, onAccessChanged }: { user: User; onAccessChanged: () => void }) {
  const [section, setSection] = useState<Section>('overview');
  const contentRef = useRef<HTMLElement>(null);
  const navigate = (target: Section) => { setSection(target); contentRef.current?.scrollTo({ top: 0 }); };
  return <div className="admin-workspace">
    <aside className="admin-sidebar" aria-label="Администрирование"><div className="admin-sidebar-title"><ShieldCheck size={20} aria-hidden="true" /><strong>Управление</strong></div>
      <nav aria-label="Разделы администрации">{[
        { id: 'overview' as const, label: 'Обзор', icon: LayoutDashboard },
        { id: 'users' as const, label: 'Пользователи', icon: Users },
        { id: 'knowledge' as const, label: 'База знаний', icon: BookOpen },
      ].map(({ id, label, icon: Icon }) => <button key={id} aria-current={section === id ? 'page' : undefined} onClick={() => navigate(id)}><Icon size={18} aria-hidden="true" />{label}</button>)}</nav>
      <div className="admin-sidebar-footer"><ShieldCheck size={17} aria-hidden="true" /><span>Доступ администратора<strong>{user.username}</strong></span></div>
    </aside>
    <main className="admin-content" ref={contentRef} id="main-content"><div className="admin-content-inner">
      {section === 'overview' && <OverviewPanel onNavigate={navigate} />}
      {section === 'users' && <UsersPanel currentUser={user} onAccessChanged={onAccessChanged} />}
      {section === 'knowledge' && <KnowledgePanel />}
    </div></main>
  </div>;
}
