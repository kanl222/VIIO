import { memo, useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { type TraceEvent, text } from '../../../types';
import { roleNames, copyToClipboard } from '../../../utils/formatters';

export const ModelStage = memo(function ModelStage({ event }: { event: TraceEvent }) {
  const [copiedHash, setCopiedHash] = useState(false);
  const [copiedContent, setCopiedContent] = useState(false);
  const [copiedMessageIdx, setCopiedMessageIdx] = useState<number | null>(null);

  const copyHash = async (hash: string) => {
    const ok = await copyToClipboard(hash);
    if (ok) {
      setCopiedHash(true);
      setTimeout(() => setCopiedHash(false), 1500);
    }
  };

  const copyContent = async (str: string) => {
    const ok = await copyToClipboard(str);
    if (ok) {
      setCopiedContent(true);
      setTimeout(() => setCopiedContent(false), 1500);
    }
  };

  const copyMessage = async (str: string, index: number) => {
    const ok = await copyToClipboard(str);
    if (ok) {
      setCopiedMessageIdx(index);
      setTimeout(() => setCopiedMessageIdx(null), 1500);
    }
  };

  if (event.stage === 'context') {
    return (
      <div className="stage-model-context">
        <div className="model-meta-header">
          <span className="model-badge">Модель: <strong>{event.model || 'не указана'}</strong></span>
          {event.prompt_sha256 ? (
            <div className="prompt-hash-pill">
              <span>SHA-256 промпта: <code>{event.prompt_sha256.slice(0, 16)}…</code></span>
              <button
                type="button"
                className="copy-mini-btn"
                onClick={() => copyHash(event.prompt_sha256 || '')}
                title="Скопировать полный SHA-256"
              >
                {copiedHash ? <Check size={11} className="good" /> : <Copy size={11} />}
              </button>
            </div>
          ) : null}
        </div>
        <p className="hint">Фактические сообщения, переданные в модель на данном шаге выполнения:</p>

        <div className="messages-list">
          {event.messages?.map((message, index) => {
            const role = roleNames[message.role] || message.role;
            const isUser = message.role === 'human' || message.role === 'user';
            const isTool = message.role === 'tool';
            const msgText = text(message.content);
            return (
              <div
                key={index}
                className={`message-bubble ${isUser ? 'user-msg' : isTool ? 'tool-msg' : 'ai-msg'}`}
              >
                <div className="message-bubble-header">
                  <div className="message-role-tag">{role}</div>
                  <button
                    type="button"
                    className="copy-mini-btn"
                    onClick={() => copyMessage(msgText, index)}
                    title="Скопировать текст сообщения"
                  >
                    {copiedMessageIdx === index ? <Check size={11} className="good" /> : <Copy size={11} />}
                    <span>{copiedMessageIdx === index ? 'Скопировано' : 'Копировать'}</span>
                  </button>
                </div>
                <div className="text message-content">{msgText}</div>
                {message.tool_calls?.length ? (
                  <div className="tool-calls-box">
                    <strong>Вызовы инструментов:</strong>
                    <pre>{text(message.tool_calls)}</pre>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  if (event.stage === 'generation') {
    const usage = event.usage;
    const promptTokens = usage?.input_tokens ?? usage?.prompt_tokens;
    const completionTokens = usage?.output_tokens ?? usage?.completion_tokens;
    const totalTokens = usage?.total_tokens ?? ((promptTokens ?? 0) + (completionTokens ?? 0) || undefined);
    const generatedText = text(event.content);

    return (
      <div className="stage-model-generation">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
          <span className="hint" style={{ margin: 0 }}>Видимый результат генерации модели:</span>
          {generatedText ? (
            <button
              type="button"
              className="copy-mini-btn"
              onClick={() => copyContent(generatedText)}
              title="Скопировать сгенерированный результат"
            >
              {copiedContent ? <Check size={11} className="good" /> : <Copy size={11} />}
              <span>{copiedContent ? 'Скопировано' : 'Копировать'}</span>
            </button>
          ) : null}
        </div>

        {event.error ? (
          <div className="bad error-box">Ошибка вызова модели: {event.error}</div>
        ) : (
          <div className="text generation-content">{generatedText}</div>
        )}

        {event.tool_calls?.length ? (
          <div className="tool-calls-section">
            <h4>Запрошенные моделью инструменты:</h4>
            <pre className="code-block">{text(event.tool_calls)}</pre>
          </div>
        ) : null}

        {totalTokens !== undefined ? (
          <div className="token-usage-bar">
            <span>
              Токены: <strong>{totalTokens.toLocaleString('ru-RU')}</strong>
              {promptTokens !== undefined && completionTokens !== undefined ? (
                <> (вход: {promptTokens.toLocaleString('ru-RU')}, выход: {completionTokens.toLocaleString('ru-RU')})</>
              ) : null}
            </span>
          </div>
        ) : null}

        <p className="hint">Показан видимый результат генерации. Скрытые рассуждения модели не фиксируются.</p>
      </div>
    );
  }

  return null;
});
