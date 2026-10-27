/** Accept display metadata only; never turn model-supplied URLs into links. */
export interface AnswerSource { title: string; document_id: string }

export function answerSources(value: unknown): AnswerSource[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const result: AnswerSource[] = [];
  for (const item of value.slice(0, 20)) {
    if (!item || typeof item.title !== 'string' || typeof item.document_id !== 'string') continue;
    const title = item.title.trim().slice(0, 500);
    const id = item.document_id.trim().slice(0, 200);
    if (!title || !id || seen.has(id + '\n' + title)) continue;
    seen.add(id + '\n' + title);
    result.push({ title, document_id: id });
  }
  return result;
}
