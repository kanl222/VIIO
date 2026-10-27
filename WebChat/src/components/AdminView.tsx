import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import {
  Activity, ArrowLeft, ArrowRight, BookOpen, Check, ChevronRight,
  Download, FileText, LayoutDashboard, Loader2, RefreshCw, Save,
  Search, ShieldCheck, Upload, Users, X, AlertCircle, Layers
} from 'lucide-react';
import * as adminApi from '../api/admin';
import type { AdminUser, CatalogDocument, Overview, BulkUploadTaskResult } from '../api/admin';
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

function PublicationPanel({ document, onClose }: { document: CatalogDocument; onClose: () => void }) {
  const [title, setTitle] = useState(document.title);
  const [content, setContent] = useState('');
  const [published, setPublished] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    adminApi.getPublication(document.id, controller.signal).then(result => {
      if (result.publication) { setTitle(result.publication.title); setContent(result.publication.content); setPublished(true); }
    }).catch(err => { if (!controller.signal.aborted) setError(errorText(err)); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [document.id]);
  const save = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(''); setSuccess('');
    try { await adminApi.publishDocument(document.id, { title, content }); setPublished(true); setSuccess('Текст опубликован для внешнего консультанта.'); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  const withdraw = async () => {
    setBusy(true); setError(''); setSuccess('');
    try { await adminApi.withdrawPublication(document.id); setPublished(false); setSuccess('Публикация отозвана. Внешний API больше не использует этот текст.'); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return <form className="document-form" onSubmit={save} aria-busy={busy}>
    <div className="section-heading"><h2>Материал для внешнего консультанта</h2><button type="button" className="icon-button" onClick={onClose} aria-label="Закрыть публикацию"><X size={18} /></button></div>
    {error && <Notice>{error}</Notice>}{success && <Notice success>{success}</Notice>}
    <p>Опубликуйте только текст, предназначенный внешним пользователям. Внутреннее содержание документа не переносится автоматически.</p>
    <label className="field">Публичное название<input value={title} maxLength={255} required disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
    <label className="field">Проверенный публичный текст<textarea rows={12} value={content} maxLength={50000} required disabled={busy} onChange={event => setContent(event.target.value)} /></label>
    <div className="form-actions"><button className="button button-primary" disabled={busy || !content.trim()}>Опубликовать для внешнего API</button>
      {published && <button type="button" className="button button-secondary" disabled={busy} onClick={withdraw}>Отозвать публикацию</button>}</div>
    <p>После выпуска новой версии документа публикацию потребуется подтвердить заново.</p>
  </form>;
}

function DocumentVersionsPanel({ docId, onClose, onUpload, onDownload }: {
  docId: string; onClose: () => void;
  onUpload: (file: File, current: CatalogDocument) => Promise<void>;
  onDownload: (doc: CatalogDocument) => void;
}) {
  const [items, setItems] = useState<CatalogDocument[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    adminApi.documentVersions(docId, controller.signal).then(result => setItems(result.items))
      .catch(err => { if (!controller.signal.aborted) setError(errorText(err)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [docId]);
  const current = items.find(item => item.is_active);
  const upload = async (file?: File) => {
    if (!file || !current) return;
    setUploading(true); setError('');
    try { await onUpload(file, current); }
    catch (err) { setError(errorText(err)); }
    finally { setUploading(false); }
  };
  return <section className="document-form" aria-labelledby="document-versions-heading" aria-busy={loading || uploading}>
    <div className="section-heading"><h2 id="document-versions-heading">Версии документа</h2><button type="button" className="icon-button" onClick={onClose} aria-label="Закрыть историю версий"><X size={18} /></button></div>
    {error && <Notice>{error}</Notice>}
    {loading ? <p role="status">Загружаем историю…</p> : <>
      <p>Поиск использует актуальную версию. Предыдущие оригиналы доступны для скачивания.</p>
      <ul className="document-versions">
        {items.map(item => <li key={item.id}><div><strong>Версия {item.version}{item.is_active ? ' — актуальная' : ''}</strong><small>{date(item.created_at)} · {item.title}</small></div>
          {item.has_original && <button className="text-button" type="button" onClick={() => onDownload(item)}><Download size={14} aria-hidden="true" /> Скачать</button>}
        </li>)}
      </ul>
      {current && <label className="file-picker"><Upload size={19} aria-hidden="true" /><span><strong>Загрузить новую версию</strong><small>Один PDF, TXT, Markdown или исходник до 10 МБ. Код доступен только программистам и администраторам.</small></span>
        <input type="file" accept=".pdf,.txt,.md,.markdown,.py,.js,.jsx,.ts,.tsx,.sql,.go,.java,.cs,.rb,.php,.rs,.yaml,.yml,.json,.toml,.sh,.ps1,.xml,.wsdl,.c,.cpp,.h" disabled={uploading} aria-label="Файл новой версии документа" onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; void upload(file); }} />
      </label>}
      {uploading && <p role="status">Сохраняем файл новой версии…</p>}
    </>}
  </section>;
}

function KnowledgePanel() {
  const [tab, setTab] = useState<'catalog' | 'upload' | 'bulk'>('catalog');

  // Каталог
  const [catalogData, setCatalogData] = useState<{ items: CatalogDocument[]; total: number } | null>(null);
  const [catalogPage, setCatalogPage] = useState(1);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState('');
  const [catalogRevision, setCatalogRevision] = useState(0);

  // Одиночная загрузка
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

  // Массовая загрузка
  const [bulkFiles, setBulkFiles] = useState<File[]>([]);
  const [bulkAccess, setBulkAccess] = useState('employees');
  const [bulkDepartments, setBulkDepartments] = useState('');
  const [bulkUploading, setBulkUploading] = useState(false);
  const [bulkError, setBulkError] = useState('');
  const [bulkTasks, setBulkTasks] = useState<BulkUploadTaskResult[]>([]);
  const bulkInputRef = useRef<HTMLInputElement>(null);
  const [versionsFor, setVersionsFor] = useState<string | null>(null);
  const [publicationFor, setPublicationFor] = useState<CatalogDocument | null>(null);
  const [uploadReview, setUploadReview] = useState<adminApi.UploadReview | null>(null);
  const [bulkAction, setBulkAction] = useState<string | null>(null);
  const reviewEditor = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (uploadReview) reviewEditor.current?.focus(); }, [uploadReview?.task_id]);
  const bulkPoll = useRef<ReturnType<typeof setInterval> | null>(null);
  const bulkMounted = useRef(true);
  useEffect(() => {
    bulkMounted.current = true;
    return () => { bulkMounted.current = false; if (bulkPoll.current) clearInterval(bulkPoll.current); };
  }, []);

  useEffect(() => {
    if (tab !== 'catalog') return;
    const controller = new AbortController();
    setCatalogLoading(true); setCatalogError('');
    adminApi.listDocuments(catalogPage, 20, '', controller.signal)
      .then(res => setCatalogData({ items: res.items, total: res.total }))
      .catch(err => { if (!controller.signal.aborted) setCatalogError(errorText(err)); })
      .finally(() => { if (!controller.signal.aborted) setCatalogLoading(false); });
    return () => controller.abort();
  }, [tab, catalogPage, catalogRevision]);

  const handleDownload = async (doc: CatalogDocument) => {
    try {
      const blob = await adminApi.downloadDocument(doc.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const suffix = ({ 'application/pdf': '.pdf', 'text/plain': '.txt', 'text/markdown': '.md' } as Record<string, string>)[doc.mime_type] || '';
      a.download = doc.original_filename || (doc.title.toLowerCase().endsWith(suffix) ? doc.title : doc.title + suffix);
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert(errorText(err));
    }
  };

  const submitSingle = async (event: FormEvent) => {
    event.preventDefault();
    if (extracting) return;
    setSaving(true); setError(''); setSuccess('');
    try {
      const result = await adminApi.addDocument({
        title, content, is_public: access === 'public',
        allowed_roles: access === 'employees' ? ['employee', 'manager'] : [],
        allowed_departments: access === 'departments' ? departments.split(',').map(d => d.trim()).filter(Boolean) : [],
      });
      setSuccess(`«${result.title}» добавлен в базу знаний. Создано фрагментов: ${result.chunks_count}.`);
      setTitle(''); setContent('');
      setExtractionInfo('');
      if (fileInput.current) fileInput.current.value = '';
      setCatalogRevision(r => r + 1);
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

  const monitorBulk = (pendingIds: string[]) => {
      if (pendingIds.length) {
        if (bulkPoll.current) clearInterval(bulkPoll.current);
        let polling = false;
        const interval = setInterval(async () => {
          if (polling) return;
          polling = true;
          try {
            const statusRes = await adminApi.getBulkStatus(pendingIds);
            if (!bulkMounted.current) return;
            setBulkTasks(previous => previous.map(task => statusRes.tasks.find(next => next.task_id === task.task_id) || task));
            const stillPending = statusRes.tasks.some(t => t.status === 'queued' || t.status === 'processing');
            if (!stillPending) {
              clearInterval(interval);
              setBulkUploading(false);
              setCatalogRevision(r => r + 1);
            }
          } catch {
            clearInterval(interval);
            if (bulkMounted.current) { setBulkError('Не удалось обновить статусы. Откройте каталог или повторите проверку позже.'); setBulkUploading(false); }
          } finally { polling = false; }
        }, 1500);
        bulkPoll.current = interval;
      } else {
        setBulkUploading(false);
      }
  };

  useEffect(() => {
    if (tab !== 'bulk' || bulkTasks.length) return;
    let active = true;
    adminApi.getBulkStatus([]).then(result => {
      if (!active) return;
      setBulkTasks(result.tasks);
      const pending = result.tasks.flatMap(task => task.task_id && (task.status === 'queued' || task.status === 'processing') ? [task.task_id] : []);
      if (pending.length) { setBulkUploading(true); monitorBulk(pending); }
    }).catch(err => { if (active) setBulkError(errorText(err)); });
    return () => { active = false; };
  }, [tab]);

  const retryBulk = async (taskId: string) => {
    setBulkError('');
    setBulkUploading(true);
    try {
      await adminApi.retryBulk(taskId);
      setBulkTasks(previous => previous.map(task => task.task_id === taskId ? { ...task, status: 'queued', error: undefined } : task));
      monitorBulk([taskId]);
    } catch (err) { setBulkError(errorText(err)); setBulkUploading(false); }
  };

  const openReview = async (taskId: string) => {
    setBulkError(''); setBulkAction(taskId);
    try { setUploadReview(await adminApi.getUploadReview(taskId)); }
    catch (err) { setBulkError(errorText(err)); }
    finally { setBulkAction(null); }
  };

  const approveReview = async () => {
    if (!uploadReview) return;
    setBulkError(''); setBulkAction(uploadReview.task_id);
    try {
      await adminApi.approveUploadReview(uploadReview);
      const taskId = uploadReview.task_id;
      setBulkTasks(previous => previous.map(task => task.task_id === taskId ? { ...task, status: 'queued' } : task));
      const pending = bulkTasks.flatMap(task => task.task_id && (task.status === 'queued' || task.status === 'processing') ? [task.task_id] : []);
      setUploadReview(null); setBulkUploading(true);
      monitorBulk([...new Set([...pending, taskId])]);
    } catch (err) { setBulkError(errorText(err)); }
    finally { setBulkAction(null); }
  };

  const cancelUpload = async (taskId: string) => {
    setBulkError(''); setBulkAction(taskId);
    try {
      await adminApi.cancelBulk(taskId);
      setBulkTasks(previous => previous.map(task => task.task_id === taskId ? { ...task, status: 'cancelled' } : task));
      if (uploadReview?.task_id === taskId) setUploadReview(null);
    } catch (err) { setBulkError(errorText(err)); }
    finally { setBulkAction(null); }
  };

  const submitBulk = async (e: FormEvent) => {
    e.preventDefault();
    if (!bulkFiles.length) return;
    setBulkUploading(true);
    setBulkError('');
    try {
      const res = await adminApi.bulkUpload(bulkFiles, {
        is_public: bulkAccess === 'public',
        allowed_roles: bulkAccess === 'employees' ? ['employee', 'manager'] : [],
        allowed_departments: bulkAccess === 'departments' ? bulkDepartments.split(',').map(d => d.trim()).filter(Boolean) : [],
      });
      setBulkTasks(res.tasks);

      const pendingIds = res.tasks.flatMap(t => t.task_id && (t.status === 'queued' || t.status === 'processing') ? [t.task_id] : []);
      monitorBulk(pendingIds);
    } catch (err) {
      setBulkError(errorText(err));
      setBulkUploading(false);
    }
  };

  const uploadVersion = async (file: File, current: CatalogDocument) => {
    if (!/\.(pdf|txt|md|markdown|py|js|jsx|ts|tsx|sql|go|java|cs|rb|php|rs|yaml|yml|json|toml|sh|ps1|xml|wsdl|c|cpp|h)$/i.test(file.name) || file.size > 10 * 1024 * 1024) throw new Error('Выберите PDF, TXT или Markdown до 10 МБ.');
    const result = await adminApi.bulkUpload([file], { is_public: current.is_public,
      allowed_roles: current.allowed_roles, allowed_departments: current.allowed_departments, version_parent_id: current.id });
    const task = result.tasks[0];
    if (!task?.task_id) throw new Error(task?.error || 'Не удалось сохранить новую версию.');
    setBulkTasks(previous => [task, ...previous]);
    setVersionsFor(null); setTab('bulk'); setBulkUploading(true);
    const pending = bulkTasks.flatMap(item => item.task_id && (item.status === 'queued' || item.status === 'processing') ? [item.task_id] : []);
    monitorBulk([...pending, task.task_id]);
  };

  const handleBulkFileSelect = (selected: FileList | null) => {
    if (!selected) return;
    const newFiles: File[] = [];
    for (let i = 0; i < selected.length; i++) {
      const f = selected[i];
      if (/\.(txt|md|markdown|pdf|py|js|jsx|ts|tsx|sql|go|java|cs|rb|php|rs|yaml|yml|json|toml|sh|ps1|xml|wsdl|c|cpp|h)$/i.test(f.name) && f.size <= 10 * 1024 * 1024) {
        newFiles.push(f);
      }
    }
    if (newFiles.length !== selected.length || bulkFiles.length + newFiles.length > 50) setBulkError('Часть файлов не выбрана: допустимы PDF, TXT, Markdown до 10 МБ, всего до 50 файлов.');
    setBulkFiles(prev => [...prev, ...newFiles].slice(0, 50));
  };

  return <>
    <div className="page-heading">
      <div>
        <h1>База знаний</h1>
        <p>Каталог документов, оригиналы и пополнение базы знаний компании.</p>
      </div>
      <div className="workspace-switcher" style={{ margin: 0 }}>
        <button aria-current={tab === 'catalog' ? 'page' : undefined} onClick={() => setTab('catalog')}>Каталог</button>
        <button aria-current={tab === 'upload' ? 'page' : undefined} onClick={() => setTab('upload')}>Новый документ</button>
        <button aria-current={tab === 'bulk' ? 'page' : undefined} onClick={() => setTab('bulk')}>Массовая загрузка</button>
      </div>
    </div>

    {tab === 'catalog' && (
      <>
        <div className="table-toolbar">
          <span className="count-label">{catalogData ? `${catalogData.total} документов в каталоге` : 'Загрузка…'}</span>
          <button className="icon-button" style={{ marginLeft: 'auto' }} onClick={() => setCatalogRevision(r => r + 1)} disabled={catalogLoading} aria-label="Обновить каталог">
            <RefreshCw size={17} className={catalogLoading ? 'animate-spin' : ''} />
          </button>
        </div>
        {catalogError ? <Notice>{catalogError}</Notice> : (
          <div className="table-container" aria-busy={catalogLoading}>
            {catalogLoading && !catalogData ? (
              <div className="loading-state" role="status"><Loader2 size={20} className="animate-spin" />Загружаем каталог…</div>
            ) : catalogData?.items.length ? (
              <table className="user-table">
                <caption className="sr-only">Каталог документов компании</caption>
                <thead>
                  <tr>
                    <th scope="col">Название</th>
                    <th scope="col">Версия</th>
                    <th scope="col">Фрагменты</th>
                    <th scope="col">Доступ</th>
                    <th scope="col">Оригинал</th>
                    <th scope="col">Дата</th>
                  </tr>
                </thead>
                <tbody>
                  {catalogData.items.map(doc => (
                    <tr key={doc.id}>
                      <td>
                        <strong>{doc.title}</strong>
                        {doc.document_kind !== 'code' && <button type="button" className="text-button" onClick={() => setPublicationFor(doc)}>Внешняя публикация</button>}
                        {doc.document_kind === 'code' && <small>Исходный код · только программистам и администраторам</small>}
                        <small style={{ display: 'block', color: 'var(--muted)', fontSize: 11 }}>{doc.id}</small>
                      </td>
                      <td><button className="text-button" type="button" onClick={() => setVersionsFor(doc.id)} aria-label={`Версии документа ${doc.title}`}>v{doc.version} · История</button></td>
                      <td>{doc.chunks_count} чанков</td>
                      <td>
                        <span className="department-text">
                          {doc.is_public ? 'Всем' : doc.allowed_roles.join(', ') || 'Ограничен'}
                        </span>
                      </td>
                      <td>
                        {doc.has_original ? (
                          <button className="text-button" onClick={() => handleDownload(doc)} title="Скачать оригинальный файл">
                            <Download size={14} aria-hidden="true" /> Скачать
                          </button>
                        ) : (
                          <span style={{ color: 'var(--muted)', fontSize: 12 }}>Без файла</span>
                        )}
                      </td>
                      <td className="date-cell">{date(doc.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="empty-state">
                <BookOpen size={28} aria-hidden="true" />
                <h2>Документы пока не добавлены</h2>
                <p>Перейдите на вкладку «Новый документ» или «Массовая загрузка», чтобы пополнить базу.</p>
                <button className="button button-primary" onClick={() => setTab('upload')}>Добавить документ</button>
              </div>
            )}
          </div>
        )}
        {!!catalogData?.total && (
          <div className="pagination">
            <span>Страница {catalogPage} из {Math.max(1, Math.ceil(catalogData.total / 20))}</span>
            <div>
              <button className="icon-button" disabled={catalogPage <= 1 || catalogLoading} onClick={() => setCatalogPage(p => p - 1)} aria-label="Предыдущая страница"><ArrowLeft size={17} /></button>
              <button className="icon-button" disabled={catalogPage * 20 >= catalogData.total || catalogLoading} onClick={() => setCatalogPage(p => p + 1)} aria-label="Следующая страница"><ArrowRight size={17} /></button>
            </div>
          </div>
        )}
      </>
    )}

    {tab === 'catalog' && versionsFor && <DocumentVersionsPanel key={`${versionsFor}-${catalogRevision}`} docId={versionsFor}
      onClose={() => setVersionsFor(null)} onUpload={uploadVersion} onDownload={handleDownload} />}
    {tab === 'catalog' && publicationFor && <PublicationPanel key={publicationFor.id} document={publicationFor} onClose={() => setPublicationFor(null)} />}

    {tab === 'upload' && (
      <div className="knowledge-layout">
        <form className="document-form" onSubmit={submitSingle} aria-busy={saving || extracting}>
          <div className="section-heading"><h2>Новый документ</h2><FileText size={19} aria-hidden="true" /></div>
          {error && <Notice>{error}</Notice>}{success && <Notice success><Check size={18} aria-hidden="true" />{success}</Notice>}
          <label className="field">Название<input required value={title} onChange={e => setTitle(e.target.value)} placeholder="Например, регламент работы с клиентами" maxLength={255} disabled={saving || extracting} /></label>
          <label className="file-picker"><Upload size={19} aria-hidden="true" /><span><strong>Выбрать файл</strong><small>PDF до 10 МБ · TXT или Markdown до 2 МБ</small></span><input ref={fileInput} type="file" accept=".pdf,.txt,.md,.markdown,.py,.js,.jsx,.ts,.tsx,.sql,.go,.java,.cs,.rb,.php,.rs,.yaml,.yml,.json,.toml,.sh,.ps1,.xml,.wsdl,.c,.cpp,.h" disabled={saving || extracting} onChange={e => void readFile(e.target.files?.[0])} aria-label="Выбрать PDF или текстовый файл" /></label>
          {extracting && <p className="field-help" role="status"><Loader2 size={16} className="animate-spin" /> Извлекаем текст. Для сканов обработка может занять несколько минут.</p>}
          {extractionInfo && <p className="field-help" role="status">{extractionInfo}</p>}
          <label className="field">Содержание<textarea required value={content} onChange={e => setContent(e.target.value)} placeholder="Вставьте текст документа…" rows={10} maxLength={500000} disabled={saving || extracting} /><small className="character-count">{content.length.toLocaleString('ru-RU')} / 500 000 символов</small></label>
          <fieldset disabled={saving || extracting}><legend>Кому доступен документ</legend><div className="access-options">{[
            ['employees', 'Сотрудникам', 'Сотрудники, руководители и программисты'], ['departments', 'Подразделениям', 'Только выбранные отделы'], ['private', 'Администраторам', 'Ограниченный доступ'], ['public', 'Всем', 'Включая гостевые запросы'],
          ].map(([value, label, hint]) => <label key={value} className={`access-option ${access === value ? 'access-selected' : ''}`}><input type="radio" name="document-access" checked={access === value} onChange={() => setAccess(value)} /><span><strong>{label}</strong><small>{hint}</small></span></label>)}</div></fieldset>
          {access === 'departments' && <label className="field">Подразделения<input required value={departments} onChange={e => setDepartments(e.target.value)} placeholder="finance, it" disabled={saving || extracting} maxLength={1950} /><small>Названия через запятую, как в профилях пользователей.</small></label>}
          <div className="form-actions"><button className="button button-primary" type="submit" disabled={saving || extracting || !title.trim() || !content.trim()}>{saving ? <Loader2 className="animate-spin" size={17} /> : <Upload size={17} />}{saving ? 'Добавляем документ…' : 'Добавить в базу знаний'}</button></div>
        </form>
        <aside className="document-help">
          <BookOpen size={25} aria-hidden="true" />
          <h2>Знания для точных ответов</h2>
          <p>Добавляйте регламенты, инструкции и ответы на частые вопросы. Текст будет разбит на фрагменты для поиска.</p>
          <h3>Проверьте перед загрузкой</h3>
          <ul><li>У документа понятное название.</li><li>Содержание актуально и не дублирует другие материалы.</li><li>Права соответствуют содержимому.</li></ul>
          <p>Администраторы и автор документа сохраняют доступ при любых настройках. Права на роль и подразделение действуют независимо: достаточно одного совпадения.</p>
        </aside>
      </div>
    )}

    {tab === 'bulk' && (
      <div className="knowledge-layout">
        <form className="document-form" onSubmit={submitBulk} aria-busy={bulkUploading}>
          <div className="section-heading"><h2>Массовая загрузка документов</h2><Layers size={19} aria-hidden="true" /></div>
          {bulkError && <Notice>{bulkError}</Notice>}

          <label className="file-picker">
            <Upload size={19} aria-hidden="true" />
            <span>
              <strong>Выбрать несколько файлов</strong>
              <small>PDF, TXT, Markdown и исходный код до 10 МБ каждый (до 50 файлов за раз)</small>
            </span>
            <input
              ref={bulkInputRef}
              type="file"
              multiple
              accept=".pdf,.txt,.md,.markdown,.py,.js,.jsx,.ts,.tsx,.sql,.go,.java,.cs,.rb,.php,.rs,.yaml,.yml,.json,.toml,.sh,.ps1,.xml,.wsdl,.c,.cpp,.h"
              disabled={bulkUploading}
              onChange={e => handleBulkFileSelect(e.target.files)}
              aria-label="Выбрать несколько файлов"
            />
          </label>

          {bulkFiles.length > 0 && (
            <div>
              <p style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>
                Выбрано файлов: {bulkFiles.length}
                <button type="button" className="text-button" style={{ marginLeft: 12 }} onClick={() => setBulkFiles([])}>Очистить</button>
              </p>
              <ul style={{ maxHeight: 150, overflowY: 'auto', border: '1px solid var(--line)', borderRadius: 8, padding: '8px 12px', fontSize: 12 }}>
                {bulkFiles.map((f, i) => (
                  <li key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
                    <span>{f.name}</span>
                    <span style={{ color: 'var(--muted)' }}>{(f.size / 1024).toFixed(1)} КБ</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <fieldset disabled={bulkUploading}><legend>Права доступа для всей партии</legend><div className="access-options">{[
            ['employees', 'Сотрудникам', 'Сотрудники, руководители и программисты'], ['departments', 'Подразделениям', 'Только выбранные отделы'], ['private', 'Администраторам', 'Ограниченный доступ'], ['public', 'Всем', 'Включая гостевые запросы'],
          ].map(([value, label, hint]) => <label key={value} className={`access-option ${bulkAccess === value ? 'access-selected' : ''}`}><input type="radio" name="bulk-access" checked={bulkAccess === value} onChange={() => setBulkAccess(value)} /><span><strong>{label}</strong><small>{hint}</small></span></label>)}</div></fieldset>

          {bulkAccess === 'departments' && <label className="field">Подразделения<input required value={bulkDepartments} onChange={e => setBulkDepartments(e.target.value)} placeholder="finance, it" disabled={bulkUploading} maxLength={1950} /><small>Названия через запятую.</small></label>}

          <div className="form-actions">
            <button className="button button-primary" type="submit" disabled={bulkUploading || bulkFiles.length === 0}>
              {bulkUploading ? <Loader2 className="animate-spin" size={17} /> : <Upload size={17} />}
              {bulkUploading ? 'Обработка в очереди…' : `Загрузить ${bulkFiles.length} файл(ов)`}
            </button>
          </div>

          {bulkTasks.length > 0 && (
            <div style={{ marginTop: 20 }}>
              <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 10 }}>Статус фоновой обработки:</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {bulkTasks.map((t, index) => (
                  <div key={t.task_id || `${t.filename}-${index}`} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', border: '1px solid var(--line)', borderRadius: 8, fontSize: 13 }}>
                    <div>
                      <strong>{t.filename}</strong>
                      {t.document_id && <small style={{ display: 'block', color: 'var(--muted)', fontSize: 11 }}>ID: {t.document_id} ({t.chunks_count} чанков)</small>}
                      {t.status === 'error' && t.task_id && <button type="button" disabled={bulkUploading} onClick={() => { if (t.task_id) retryBulk(t.task_id); }}>Повторить обработку</button>}
                      {t.status === 'needs_review' && t.task_id && <button type="button" className="button button-secondary" disabled={bulkAction !== null} onClick={() => { if (t.task_id) openReview(t.task_id); }}>Проверить текст</button>}
                      {(t.status === 'queued' || t.status === 'needs_review') && t.task_id && <button type="button" className="button button-secondary" disabled={bulkAction !== null} onClick={() => { if (t.task_id) cancelUpload(t.task_id); }}>Отменить загрузку</button>}
                      {t.error && <small style={{ display: 'block', color: 'var(--negative)', fontSize: 11 }}>{t.error}</small>}
                    </div>
                    <span className={`status-badge ${t.status === 'ready' ? 'status-good' : t.status === 'error' ? 'status-bad' : ''}`}>
                      {t.status === 'ready' && <><Check size={12} /> Готово</>}
                      {t.status === 'error' && <><AlertCircle size={12} /> Ошибка</>}
                      {t.status === 'processing' && <><Loader2 size={12} className="animate-spin" /> Обработка</>}
                      {t.status === 'queued' && 'В очереди'}
                      {t.status === 'needs_review' && 'Требует проверки'}
                      {t.status === 'cancelled' && 'Отменено'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {uploadReview && <section className="upload-review" aria-labelledby="upload-review-heading" aria-busy={bulkAction !== null}>
            <h3 id="upload-review-heading">Проверка текста: {uploadReview.filename}</h3>
            <p>Страниц: {uploadReview.pages}. Исправьте ошибки распознавания. Исходный PDF сохранится без изменений.</p>
            <div className="field"><label htmlFor="upload-review-content">Текст для базы знаний</label>
            <textarea id="upload-review-content" ref={reviewEditor} rows={16} maxLength={500000}
              value={uploadReview.content} disabled={bulkAction !== null}
              onChange={event => setUploadReview(previous => previous ? { ...previous, content: event.target.value } : null)} /></div>
            <div className="form-actions">
              <button type="button" className="button button-primary" disabled={bulkAction !== null || !uploadReview.content.trim()} onClick={approveReview}>Подтвердить и добавить в базу</button>
              <button type="button" className="button button-secondary" disabled={bulkAction !== null} onClick={() => setUploadReview(null)}>Вернуться к списку</button>
            </div>
            <p>После подтверждения текст станет доступен по правам, выбранным при загрузке.</p>
          </section>}
        </form>

        <aside className="document-help">
          <Layers size={25} aria-hidden="true" />
          <h2>Пакетный импорт</h2>
          <p>Файлы помещаются в устойчивую очередь обработки. Оригинал каждого файла сохраняется. Текст PDF добавляется в базу после проверки администратором.</p>
          <h3>Особенности</h3>
          <ul>
            <li>Каждый файл получает независимый статус.</li>
            <li>Исходный код доступен только программистам и администраторам независимо от общих прав партии.</li>
            <li>Ошибочный или повреждённый файл не останавливает обработку остальных.</li>
            <li>Одинаковые файлы автоматически распознаются по хешу.</li>
            <li>Задание в очереди или на проверке можно отменить. Начавшуюся обработку отменить нельзя.</li>
            <li>После завершения все файлы доступны в каталоге и для скачивания.</li>
          </ul>
        </aside>
      </div>
    )}
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
