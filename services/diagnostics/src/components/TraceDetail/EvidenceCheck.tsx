import { memo } from 'react';
import { CheckCircle2, XCircle, HelpCircle } from 'lucide-react';
import { type Trace, text } from '../../types';

interface EvidenceCheckProps {
  trace: Trace;
  evidence: string;
  documentId: string;
}

export const EvidenceCheck = memo(function EvidenceCheck({
  trace,
  evidence,
  documentId: rawId,
}: EvidenceCheckProps) {
  const needle = evidence.trim().toLocaleLowerCase('ru');
  const documentId = rawId.trim();

  if (!needle && !documentId) {
    return (
      <div className="evidence-checker-empty">
        <p className="hint">
          Укажите точную фразу из инструкции или идентификатор документа выше, чтобы проверить, на каких этапах цепочки они присутствуют.
        </p>
      </div>
    );
  }

  const searches = trace.events.filter(event => event.stage === 'retrieval');
  const hasPhrase = (value: unknown): boolean =>
    text(value).toLocaleLowerCase('ru').includes(needle);

  const fragmentsCheck = (field: 'candidates' | 'selected'): boolean | null => {
    const fragments = searches
      .flatMap(event => event[field] ?? [])
      .filter(doc => !documentId || doc.document_id === documentId);

    if (fragments.some(doc => !needle || (doc.text !== undefined && hasPhrase(doc.text)))) {
      return true;
    }
    if (
      !searches.length ||
      searches.some(event => !event[field]) ||
      (needle && fragments.some(doc => doc.text === undefined))
    ) {
      return null;
    }
    return false;
  };

  const contexts = trace.events.filter(event => event.stage === 'context');
  const answers = trace.events.filter(event => event.stage === 'answer');

  const contextFound =
    needle && contexts.length
      ? contexts.some(event =>
          hasPhrase(event.messages?.map(message => text(message.content)).join('\n'))
        )
      : null;

  const answerFound = needle && answers.length ? answers.some(event => hasPhrase(event.reply)) : null;

  const stages: { label: string; found: boolean | null; desc: string }[] = [
    {
      label: 'Кандидаты поиска',
      found: fragmentsCheck('candidates'),
      desc: 'Векторный и лексический поиск',
    },
    {
      label: 'Отобранные фрагменты',
      found: fragmentsCheck('selected'),
      desc: 'После переранжирования и разнообразия',
    },
    {
      label: 'Вход модели (контекст)',
      found: contextFound,
      desc: 'Системный и пользовательский промпт',
    },
    {
      label: 'Итоговый ответ',
      found: answerFound,
      desc: 'Текст в финальном ответе пользователю',
    },
  ];

  return (
    <div className="evidence-checker-card">
      <div className="evidence-grid">
        {stages.map(({ label, found, desc }) => {
          const statusClass = found === true ? 'found' : found === false ? 'missing' : 'unknown';
          return (
            <div key={label} className={`evidence-cell ${statusClass}`}>
              <div className="cell-header">
                {found === true ? (
                  <CheckCircle2 size={16} className="status-icon good" />
                ) : found === false ? (
                  <XCircle size={16} className="status-icon bad" />
                ) : (
                  <HelpCircle size={16} className="status-icon muted" />
                )}
                <strong>{label}</strong>
              </div>
              <div className="cell-status">
                {found === true ? 'Найдено' : found === false ? 'Не найдено' : 'Нет данных'}
              </div>
              <span className="cell-desc">{desc}</span>
            </div>
          );
        })}
      </div>
      <p className="hint evidence-hint">
        Проверяется буквальное вхождение фразы без учёта регистра. Это инструмент быстрой локализации потерь контекста в пайплайне RAG.
      </p>
    </div>
  );
});
