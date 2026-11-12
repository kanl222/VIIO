import { memo, useDeferredValue, useState, useEffect, type FormEvent } from 'react';
import { Save, AlertCircle, CheckCircle } from 'lucide-react';
import { api, blankReview, stageNames, type Review, type Trace } from '../../types';
import { EvidenceCheck } from './EvidenceCheck';

interface ReviewPanelProps {
  trace: Trace;
  onSaved: (id: string, review: Review) => void;
}

export const ReviewPanel = memo(function ReviewPanel({ trace, onSaved }: ReviewPanelProps) {
  const [review, setReview] = useState<Review>(() => ({ ...blankReview, ...trace.review }));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  // Sync state when selected trace changes
  useEffect(() => {
    setReview({ ...blankReview, ...trace.review });
    setMessage('');
    setError('');
  }, [trace.id, trace.review]);

  const deferredEvidence = useDeferredValue(review.evidence);
  const deferredDocId = useDeferredValue(review.document_id);

  const change = <K extends keyof Review>(key: K, value: Review[K]) => {
    setReview(prev => ({ ...prev, [key]: value }));
    setMessage('');
    setError('');
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setMessage('');
    setError('');

    try {
      const saved = await api<Review>(`/api/reviews/${trace.id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(review),
      });
      onSaved(trace.id, saved);
      setMessage('Разбор успешно сохранён в эталонной выборке');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось сохранить разбор. Повторите попытку.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="review-section" aria-label="Сравнение с эталоном и разбор качества">
      <div className="section-header">
        <h2>Сравнение с эталоном и разбор качества</h2>
        <p className="hint">
          Оцените ответ и отметьте первый этап, на котором потерялись нужные факты. Данные эталона сохраняются локально для бенчмаркинга и регрессионного тестирования.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="review-form">
        <fieldset disabled={saving} className="review-fieldset">
          <div className="review-grid">
            <div className="form-group">
              <label htmlFor="review-stage">Результат разбора (первая ошибка):</label>
              <select
                id="review-stage"
                value={review.stage}
                onChange={e => change('stage', e.target.value)}
                className="select-input"
              >
                <option value="">Без оценки</option>
                {Object.entries(stageNames).map(([key, name]) => (
                  <option key={key} value={key}>
                    {name}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label htmlFor="review-score">Оценка качества ответа:</label>
              <select
                id="review-score"
                value={review.score ?? ''}
                onChange={e => change('score', e.target.value ? Number(e.target.value) : null)}
                className="select-input"
              >
                <option value="">Не оценено</option>
                {[5, 4, 3, 2, 1].map(n => (
                  <option key={n} value={n}>
                    {n} {n === 5 ? '★ (Идеально)' : n === 1 ? '★ (Неверно)' : '★'}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group full-width">
              <label htmlFor="review-expected">Ожидаемый эталонный ответ:</label>
              <textarea
                id="review-expected"
                maxLength={6000}
                rows={3}
                value={review.expected_answer}
                onChange={e => change('expected_answer', e.target.value)}
                placeholder="Что должен был ответить агент и на какие факты / документы опереться..."
                className="textarea-input"
              />
            </div>

            <div className="form-group">
              <label htmlFor="review-evidence">Точная фраза из источника:</label>
              <textarea
                id="review-evidence"
                maxLength={6000}
                rows={2}
                value={review.evidence}
                onChange={e => change('evidence', e.target.value)}
                placeholder="Фраза для проверки присутствия в контексте"
                className="textarea-input"
              />
            </div>

            <div className="form-group">
              <label htmlFor="review-docid">Идентификатор документа (document_id):</label>
              <input
                id="review-docid"
                maxLength={64}
                value={review.document_id}
                onChange={e => change('document_id', e.target.value)}
                placeholder="Например, doc-xxx или ID публикации"
                className="text-input"
              />
              <span className="field-hint">Используется для проверки наличия документа в кандидатах</span>
            </div>

            <div className="form-group full-width">
              <label>Проверка фактов и контекста в пайплайне:</label>
              <EvidenceCheck
                trace={trace}
                evidence={deferredEvidence}
                documentId={deferredDocId}
              />
            </div>

            <div className="form-group full-width">
              <label htmlFor="review-notes">Заметки инженера / QA:</label>
              <textarea
                id="review-notes"
                maxLength={6000}
                rows={2}
                value={review.notes}
                onChange={e => change('notes', e.target.value)}
                placeholder="Причины сбоя, предложения по тюнингу промпта или эмбеддингов..."
                className="textarea-input"
              />
            </div>
          </div>

          <div className="review-actions-bar">
            <button type="submit" className="primary-action-btn" disabled={saving}>
              <Save size={16} aria-hidden="true" />
              <span>{saving ? 'Сохраняем…' : 'Сохранить разбор'}</span>
            </button>

            {message ? (
              <div className="save-status good" role="status">
                <CheckCircle size={16} aria-hidden="true" />
                <span>{message}</span>
              </div>
            ) : null}

            {error ? (
              <div className="save-status bad" role="alert">
                <AlertCircle size={16} aria-hidden="true" />
                <span>{error}</span>
              </div>
            ) : null}
          </div>
        </fieldset>
      </form>
    </section>
  );
});
