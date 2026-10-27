import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
function loadSource(path, mocks) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  });
  const exports = {};
  vm.runInNewContext(outputText, {
    exports, require: name => mocks[name] ?? require(name), Date, console,
  });
  return exports;
}

test('saved assistant messages show one source list rather than model and attachment duplicates', () => {
  const { ChatMessage } = loadSource('../src/components/ChatMessage.tsx', {});
  const title = 'Плюсы ИС ЛО по сравнению с ЕЦП';
  const html = renderToStaticMarkup(React.createElement(ChatMessage, { message: {
    id: 'source-replay', sender: 'assistant', text: `Ответ по документам.\n\nИсточники:\n- Материал «${title}»`,
    attachments: [{ type: 'knowledge_source', name: title, data: { document_id: 'doc-one' } }],
  } }));
  assert.equal(html.split(title).length - 1, 1);
  assert(html.includes('Ответ по документам.'));
  assert(html.includes('Предоставленные материалы'));
});

test('citation cleanup preserves code and explanations after a source heading', () => {
  const { stripDuplicateSources } = loadSource('../src/components/ChatMessage.tsx', {});
  const code = '```text\nИсточники:\n- Материал «Регламент»\n```';
  assert.equal(stripDuplicateSources(code + '\n\n**Источники:**\n- Материал «Регламент»', ['Регламент']), code);
  const explanation = 'Источники:\n- Регламент\n\nПрименяется только для филиалов.';
  assert.equal(stripDuplicateSources(explanation, ['Регламент']), explanation);
});

test('citation cleanup keeps text without attachments and unknown source titles', () => {
  const { stripDuplicateSources } = loadSource('../src/components/ChatMessage.tsx', {});
  const text = 'Ответ.\n\nИсточники:\n- Регламент';
  assert.equal(stripDuplicateSources(text, []), text);
  assert.equal(stripDuplicateSources(text, ['Другой документ']), text);
});

test('formatted quoted source titles are matched while inline attribution is retained', () => {
  const { stripDuplicateSources } = loadSource('../src/components/ChatMessage.tsx', {});
  const text = 'В материале «ИС "ЛО"» описаны права.\n\n### Источники:\n1. Материал «ИС "ЛО"»\n2. **Регистр.**';
  assert.equal(stripDuplicateSources(text, ['ИС "ЛО"', 'Регистр.']), 'В материале «ИС "ЛО"» описаны права.');
});

test('duplicate structured attachments are shown once and user messages are unchanged', () => {
  const { ChatMessage } = loadSource('../src/components/ChatMessage.tsx', {});
  const attachment = { type: 'knowledge_source', name: 'Регламент', data: { document_id: 'doc-one' } };
  const assistant = renderToStaticMarkup(React.createElement(ChatMessage, { message: { id:'a',sender:'assistant',text:'Ответ',attachments:[attachment,attachment] } }));
  assert.equal(assistant.split('Регламент').length - 1, 1);
  const user = renderToStaticMarkup(React.createElement(ChatMessage, { message: { id:'u',sender:'user',text:'Источники:\n- Регламент',attachments:[attachment] } }));
  assert(user.includes('Источники:'));
  assert(!user.includes('Предоставленные материалы'));
});

// Small hook runner for deterministic request ordering without a browser or timers.
function chatHarness(api) {
  const slots = [];
  let cursor = 0;
  let effects = [];
  const changed = (a, b) => !a || a.length !== b.length || a.some((v, i) => !Object.is(v, b[i]));
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], next => { slots[index] = typeof next === 'function' ? next(slots[index]) : next; }];
    },
    useRef(initial) {
      const index = cursor++;
      return slots[index] ??= { current: initial };
    },
    useCallback(fn, deps) {
      const index = cursor++;
      if (!slots[index] || changed(slots[index].deps, deps)) slots[index] = { fn, deps };
      return slots[index].fn;
    },
    useEffect(fn, deps) {
      const index = cursor++;
      if (!slots[index] || changed(slots[index].deps, deps)) {
        const cleanup = slots[index]?.cleanup;
        slots[index] = { deps };
        effects.push(() => { cleanup?.(); slots[index].cleanup = fn(); });
      }
    },
  };
  const { useChat } = loadSource('../src/hooks/useChat.ts', {
    react, '../api/conversations': api, '../api/chat': api,
  });
  return id => {
    cursor = 0;
    const result = useChat(id);
    const pending = effects;
    effects = [];
    pending.forEach(run => run());
    return result;
  };
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

test('a late history response cannot replace the selected conversation', async () => {
  const a = deferred(), b = deferred();
  const render = chatHarness({ getConversation: id => id === 'a' ? a.promise : b.promise });
  render('a');
  render('b');
  b.resolve({ messages: [{ id: 'b-message' }] });
  await flush();
  a.resolve({ messages: [{ id: 'a-message' }] });
  await flush();
  assert.equal(render('b').messages[0].id, 'b-message');
});

test('a reply from another conversation cannot appear in the selected chat', async () => {
  const reply = deferred();
  const render = chatHarness({
    getConversation: async () => ({ messages: [] }),
    sendMessage: () => reply.promise,
  });
  render('a');
  await flush();
  const sending = render('a').sendMessage('question');
  render('b');
  await flush();
  reply.resolve({ reply: 'answer for a' });
  await sending;
  assert.equal(render('b').messages.length, 0);
});

test('history loaded during a send cannot erase optimistic messages', async () => {
  const history = deferred(), reply = deferred();
  const render = chatHarness({ getConversation: () => history.promise, sendMessage: () => reply.promise });
  const chat = render('new');
  const sending = chat.sendMessage('first question');
  history.resolve({ messages: [] });
  await flush();
  reply.resolve({ reply: 'first answer' });
  await sending;
  assert.deepEqual(Array.from(render('new').messages, message => message.text), ['first question', 'first answer']);
});

test('returning to an in-flight chat restores its messages, not another chat history', async () => {
  const reply = deferred();
  const render = chatHarness({
    getConversation: async id => ({ messages: [{ id: `${id}-history`, text: id }] }),
    sendMessage: () => reply.promise,
  });
  render('a');
  await flush();
  const sending = render('a').sendMessage('question');
  render('b');
  await flush();
  render('a');
  assert.deepEqual(Array.from(render('a').messages, message => message.text), ['a', 'question']);
  reply.resolve({ reply: 'answer' });
  await sending;
  assert.equal(render('a').messages.at(-1).text, 'answer');
});

test('the first prompt is sent with the newly created conversation ID', async () => {
  let captured;
  const component = () => null;
  const { App } = loadSource('../src/App.tsx', {
    react: { useState: initial => [initial, () => {}] },
    './hooks/useAuth': { useAuth: () => ({ user: { id: 'user' } }) },
    './hooks/useConversations': { useConversations: () => ({
      conversations: [], currentId: null, createConversation: async () => ({ id: 'new-conversation' }),
    }) },
    './hooks/useChat': { useChat: () => ({ sendMessage: (...args) => { captured = args; } }) },
    ...Object.fromEntries(['Header', 'Sidebar', 'ChatView', 'ChatInput', 'AuthModal', 'AdminView'].map(name => [
      `./components/${name}`, { [name]: component },
    ])),
  });
  function findInput(node) {
    if (!node || typeof node !== 'object') return;
    if (node.props?.onSendMessage) return node;
    for (const child of [node.props?.children].flat()) {
      const found = findInput(child);
      if (found) return found;
    }
  }
  await findInput(App()).props.onSendMessage('hello');
  assert.equal(captured?.[0], 'hello');
  assert.equal(captured?.[1], 'new-conversation');
});

test('the hook sends a first message before the new selection is committed', async () => {
  const reply = deferred();
  let sentId;
  const render = chatHarness({
    getConversation: async () => ({ messages: [] }),
    sendMessage: (id) => { sentId = id; return reply.promise; },
  });
  const sending = render(null).sendMessage('first', 'created');
  render('created');
  await flush();
  assert.equal(sentId, 'created');
  assert.equal(render('created').messages[0].text, 'first');
  reply.resolve({ reply: 'answer' });
  await sending;
  assert.equal(render('created').messages[1].text, 'answer');
});
