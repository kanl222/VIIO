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
    exports, require: name => mocks[name] ?? require(name), Date, console, AbortController,
  });
  return exports;
}

test('admin overview keeps healthy agent status consistent and displays provider token usage', () => {
  const overview = {
    counts: { users: 1, conversations: 2, messages: 3, sessions: 1 },
    database: true, agent: { healthy: true, chunks: 0 }, documents: { healthy: false },
    usage: { input_tokens: 100, output_tokens: 20, total_tokens: 120, reported_calls: 2,
      unreported_calls: 1, started_at: '2026-11-06T00:00:00Z' },
    checked_at: '2026-11-06T00:00:00Z',
  };
  const { AdminView } = loadSource('../src/components/AdminView.tsx', {
    react: { ...React, useState: initial => [initial === null ? overview : initial === true ? false : initial, () => {}] },
    '../api/admin': {},
  });
  const html = renderToStaticMarkup(React.createElement(AdminView, { user: { id: 'admin', role: 'admin' } }));
  assert(html.includes('Сервис работает · модель не указана'));
  assert(!html.includes('Нет подключения к сервису'));
  assert(html.includes('Недоступен'));
  assert(html.includes('Расход токенов'));
  assert(html.includes('Входящие') && html.includes('Исходящие') && html.includes('120'));
  assert(html.includes('Счётчики сбрасываются при перезапуске'));
  assert(html.includes('Вызовы без данных о токенах: 1'));
});

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

test('catalog download paths render as links while unrelated links remain text', () => {
  const { ChatMessage } = loadSource('../src/components/ChatMessage.tsx', {});
  const path = '/api/chat/download/doc-0123456789abcdef0123456789abcdef';
  const html = renderToStaticMarkup(React.createElement(ChatMessage, { message: {
    id: 'catalog-downloads', sender: 'assistant',
    text: '1. Руководство — [Скачать](' + path + ')\n2. Инструкция — /api/chat/download/doc-01234567\n[Другое](javascript:alert(1))',
  } }));
  assert(html.includes('href="' + path + '"'));
  assert(html.includes('href="/api/chat/download/doc-01234567"'));
  assert(!html.includes('href="javascript:'));
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

// Небольшой раннер хуков для детерминированного порядка запросов без браузера и таймеров.
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

test('manual mode belongs to the upper chat toolbar and is forwarded separately', () => {
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const input = readFileSync(new URL('../src/components/ChatInput.tsx', import.meta.url), 'utf8');
  assert.ok(app.indexOf('className="chat-toolbar"') < app.indexOf('<ChatInput'));
  assert.match(app, /agent_mode: canUseSpecialists \? agentMode : 'assistant'/);
  assert.match(app, /id="agent-mode"/);
  assert.doesNotMatch(input, /<select/);
});

test('toolbar selection drives the next request and specialist modes respect the user role', async () => {
  for (const role of ['programmer', 'employee']) {
    const slots = []; let cursor = 0; let captured;
    const component = () => null;
    const { App } = loadSource('../src/App.tsx', {
      react: { useState(initial) {
        const index = cursor++;
        if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
        return [slots[index], value => { slots[index] = value; }];
      } },
      './hooks/useAuth': { useAuth: () => ({ user: { id: 'user', role } }) },
      './hooks/useConversations': { useConversations: () => ({ conversations: [], currentId: 'chat' }) },
      './hooks/useChat': { useChat: () => ({ sendMessage: (...args) => { captured = args; } }) },
      ...Object.fromEntries(['Header', 'Sidebar', 'ChatView', 'ChatInput', 'AuthModal', 'AdminView'].map(name => [
        `./components/${name}`, { [name]: component },
      ])),
    });
    const find = (node, predicate) => {
      if (!node || typeof node !== 'object') return;
      if (predicate(node)) return node;
      for (const child of [node.props?.children].flat(Infinity)) {
        const result = find(child, predicate); if (result) return result;
      }
    };
    const render = () => { cursor = 0; return App(); };
    let select = find(render(), node => node.props?.id === 'agent-mode');
    const options = select.props.children.flat(Infinity).filter(child => child?.type === 'option');
    assert.equal(options.length, role === 'programmer' ? 3 : 1);
    select.props.onChange({ target: { value: 'database' } });
    const tree = render();
    select = find(tree, node => node.props?.id === 'agent-mode');
    assert.equal(select.props.value, role === 'programmer' ? 'database' : 'assistant');
    await find(tree, node => node.props?.onSendMessage).props.onSendMessage('show schemas');
    assert.equal(captured[2].agent_mode, role === 'programmer' ? 'database' : 'assistant');
  }
});

test('cloud selection survives chat creation and hook forwarding', async () => {
  let captured;
  const render = chatHarness({ getConversation: async () => ({ messages: [] }),
    sendMessage: async (...args) => { captured = args; return { reply: 'Public answer' }; } });
  const options = { rag_backend: 'yandex', confirmed_public_for_external_cloud: true };
  await render(null).sendMessage('Public question', 'new-public-chat', options);
  assert.equal(captured[0], 'new-public-chat');
  assert.equal(captured[2], options);
});

test('user can abort an in-flight request from the chat hook', async () => {
  const pending = deferred();
  let signal;
  const render = chatHarness({ getConversation: async () => ({ messages: [] }),
    sendMessage: (...args) => { signal = args[5]; return pending.promise; } });
  const sending = render('abort-chat').sendMessage('Question');
  assert.equal(signal.aborted, false);
  render('abort-chat').cancelRequest();
  assert.equal(signal.aborted, true);
  const error = new Error('Aborted'); error.name = 'AbortError';
  pending.reject(error);
  await sending;
  assert.equal(render('abort-chat').submitting, false);
});

test('chat gateway rejects missing cloud consent and forwards explicit RAG selection', async () => {
  const { createChatRouter } = await import('../dist-server/routes/chatRoutes.js');
  let forwarded; let queries = 0;
  const pool = { query: async sql => { queries++; return { rows: sql.includes('SELECT id, agent_thread_id')
    ? [{ id: 'conv', agent_thread_id: 'stored-thread' }] : [{ id: 'message' }] }; } };
  const router = createChatRouter(() => pool, () => {}, async (_url, options) => {
    forwarded = JSON.parse(options.body);
    return new Response(JSON.stringify({ reply: 'Public answer', sources: [] }), { status: 200 });
  });
  const handler = router.stack.find(layer => layer.route?.methods.post).route.stack[0].handle;
  const res = { statusCode: 200, status(code) { this.statusCode = code; return this; },
    json(value) { this.body = value; return this; } };
  const req = { user: { id: 'trusted-user', role: 'employee', departments: [] },
    body: { conversation_id: 'conv', message: 'Public question', rag_backend: 'yandex' } };
  await handler(req, res);
  assert.equal(res.statusCode, 403); assert.equal(queries, 0); assert.equal(forwarded, undefined);
  req.body.confirmed_public_for_external_cloud = true;
  res.statusCode = 200;
  await handler(req, res);
  assert.equal(res.statusCode, 200);
  assert.equal(forwarded.rag_backend, 'yandex');
  assert.equal(forwarded.confirmed_public_for_external_cloud, true);
  assert.equal(forwarded.user_context.user_id, 'trusted-user');
});

test('chat gateway validates manual modes and hides database content from history', async () => {
  const { createChatRouter } = await import('../dist-server/routes/chatRoutes.js');
  let forwarded;
  const writes = [];
  const pool = { query: async (sql, args) => {
    if (sql.includes('INSERT INTO messages')) writes.push(args);
    return { rows: sql.includes('SELECT id, agent_thread_id')
      ? [{ id: 'conv', agent_thread_id: 'thread' }] : [{ id: 'message' }] };
  } };
  const router = createChatRouter(() => pool, () => {}, async (_url, options) => {
    forwarded = JSON.parse(options.body);
    return new Response(JSON.stringify({ reply: 'Private answer', sources: [] }));
  });
  const handler = router.stack.find(layer => layer.route?.methods.post).route.stack[0].handle;
  const res = { statusCode: 200, status(code) { this.statusCode = code; return this; }, json(value) { this.body = value; return this; } };
  const req = { user: { id: 'user', role: 'employee' },
    body: { conversation_id: 'conv', message: 'Private query', agent_mode: 'database' } };
  await handler(req, res);
  assert.equal(res.statusCode, 403);
  assert.equal(forwarded, undefined);
  req.user.role = 'programmer'; res.statusCode = 200;
  await handler(req, res);
  assert.equal(res.statusCode, 200);
  assert.equal(forwarded.agent_mode, 'database');
  assert.equal(forwarded.message, 'Private query');
  assert(!JSON.stringify(writes).includes('Private query'));
  assert(!JSON.stringify(writes).includes('Private answer'));
  req.body.agent_mode = 'invalid';
  await handler(req, res);
  assert.equal(res.statusCode, 400);
});

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

test('streamed deltas update a single assistant bubble and final reply replaces provisional text', async () => {
  const reply = deferred();
  let emit;
  const render = chatHarness({ getConversation: async () => ({ messages: [] }),
    sendMessage: (_id, _text, _options, onEvent) => { emit = onEvent; return reply.promise; } });
  render('chat'); await flush();
  const sending = render('chat').sendMessage('Question');
  emit({ type: 'delta', text: 'Part ' });
  emit({ type: 'delta', text: 'one' });
  assert.equal(render('chat').messages.at(-1).text, 'Part one');
  assert.equal(render('chat').messages.length, 2);
  emit({ type: 'plan', steps: [{ title: 'Read source', status: 'in_progress' }] });
  assert.equal(render('chat').plan[0].title, 'Read source');
  emit({ type: 'reset' });
  assert.equal(render('chat').messages.length, 1);
  emit({ type: 'delta', text: 'Draft answer' });
  reply.resolve({ reply: 'Verified final answer', message_id: 'saved' });
  await sending;
  assert.equal(render('chat').messages.at(-1).text, 'Verified final answer');
  assert.equal(render('chat').messages.at(-1).id, 'saved');
  assert.equal(render('chat').messages.length, 2);
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

test('sending a message keeps chat history visible instead of loading the whole conversation', () => {
  const component = () => null;
  const { ChatView } = loadSource('../src/components/ChatView.tsx', {
    './ChatMessage': { ChatMessage: ({ message }) => React.createElement('p', null, message.text) },
  });
  const { App } = loadSource('../src/App.tsx', {
    react: { useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}] },
    './hooks/useAuth': { useAuth: () => ({ user: { id: 'user' }, loading: false }) },
    './hooks/useConversations': { useConversations: () => ({
      conversations: [{ id: 'current', title: 'Conversation' }], currentId: 'current', loading: false,
    }) },
    './hooks/useChat': { useChat: () => ({
      messages: [{ id: 'previous', text: 'Previous answer' }, { id: 'new', text: 'New question' }],
      loading: false, submitting: true,
    }) },
    './components/ChatView': { ChatView },
    ...Object.fromEntries(['Header', 'Sidebar', 'ChatInput', 'AuthModal', 'AdminView'].map(name => [
      `./components/${name}`, { [name]: component },
    ])),
  });
  const html = renderToStaticMarkup(App());
  assert(!html.includes('Загружаем диалог'));
  assert(html.includes('Previous answer'));
  assert(html.includes('New question'));
  assert(html.includes('Ассистент готовит ответ'));
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
