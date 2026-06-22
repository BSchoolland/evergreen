// Deterministic contract test for ClaudeThread via an injected fake queryFn.
// No network. Drives the bridge through SDK-shaped messages and the canUseTool gate.
import assert from 'node:assert/strict';
import { ClaudeThread } from '../claudeThread.js';

const results = [];
function check(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => { results.push({ name, ok: true }); console.log(`PASS  ${name}`); })
    .catch((err) => { results.push({ name, ok: false, err: err?.message || String(err) }); console.log(`FAIL  ${name}: ${err?.message || err}`); });
}

// Capture every emitted event in order. We attach broad listeners.
function recorder(ct) {
  const events = [];
  const names = ['agentStart', 'textStart', 'text', 'textEnd', 'toolStart', 'toolEnd',
                 'uiRequest', 'status', 'cmdError', 'session', 'agentEnd', 'idle', 'exit'];
  for (const n of names) {
    ct.on(n, (a, b) => events.push({ name: n, a, b }));
  }
  return events;
}

const tick = () => new Promise((r) => setImmediate(r));
async function waitFor(pred, { timeout = 2000 } = {}) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeout) throw new Error('waitFor timed out');
    await tick();
  }
}

// A pushable async iterable used as the fake SDK output stream.
function makeStream() {
  const queue = [];
  let waiter = null;
  let closed = false;
  const push = (msg) => { queue.push(msg); if (waiter) { const w = waiter; waiter = null; w(); } };
  const close = () => { closed = true; if (waiter) { const w = waiter; waiter = null; w(); } };
  const iterable = {
    async *[Symbol.asyncIterator]() {
      while (true) {
        while (queue.length) yield queue.shift();
        if (closed) return;
        await new Promise((res) => { waiter = res; });
      }
    },
  };
  return { iterable, push, close };
}

// ---------------------------------------------------------------------------
// 1. Streaming order + concatenated deltas
// ---------------------------------------------------------------------------
await check('1 streaming order + concatenated deltas', async () => {
  const stream = makeStream();
  const queryFn = () => stream.iterable;
  const ct = new ClaudeThread('t1', { queryFn });
  const events = recorder(ct);

  ct.prompt('hi'); // -> agentStart (from prompt)
  await tick();

  stream.push({ type: 'system', subtype: 'init', session_id: 'sess-1' });
  stream.push({ type: 'stream_event', event: { type: 'content_block_start', content_block: { type: 'text' } } });
  const parts = ['Hel', 'lo ', 'wor', 'ld'];
  for (const p of parts) {
    stream.push({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text: p } } });
  }
  stream.push({ type: 'result', subtype: 'success', session_id: 'sess-1' });

  await waitFor(() => events.some((e) => e.name === 'agentEnd'));

  const order = events.filter((e) => ['agentStart', 'session', 'textStart', 'text', 'textEnd', 'agentEnd'].includes(e.name)).map((e) => e.name);
  // collapse repeated 'text'
  const collapsed = order.filter((n, i) => !(n === 'text' && order[i - 1] === 'text'));
  assert.deepEqual(collapsed, ['agentStart', 'session', 'textStart', 'text', 'textEnd', 'agentEnd'],
    `got order ${JSON.stringify(collapsed)}`);

  const text = events.filter((e) => e.name === 'text').map((e) => e.a).join('');
  assert.equal(text, 'Hello world', `concatenated text was ${JSON.stringify(text)}`);

  const sess = events.find((e) => e.name === 'session');
  assert.equal(sess.a, 'sess-1');

  stream.close();
  ct.shutdown();
});

// ---------------------------------------------------------------------------
// 2. Tool events: tool_use -> tool_result (isError correct)
// ---------------------------------------------------------------------------
await check('2 tool events toolStart/toolEnd', async () => {
  const stream = makeStream();
  const ct = new ClaudeThread('t2', { queryFn: () => stream.iterable });
  const events = recorder(ct);

  ct.prompt('run a tool');
  await tick();
  stream.push({ type: 'system', subtype: 'init', session_id: 's2' });
  stream.push({ type: 'assistant', message: { content: [{ type: 'tool_use', id: 'tu_1', name: 'Read', input: { path: '/x' } }] } });
  stream.push({ type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: 'tu_1', is_error: true }] } });
  stream.push({ type: 'result', subtype: 'success', session_id: 's2' });

  await waitFor(() => events.some((e) => e.name === 'toolEnd'));

  const ts = events.find((e) => e.name === 'toolStart');
  assert.ok(ts, 'toolStart emitted');
  assert.equal(ts.a.toolName, 'Read');
  assert.deepEqual(ts.a.args, { path: '/x' });

  const te = events.find((e) => e.name === 'toolEnd');
  assert.ok(te, 'toolEnd emitted');
  assert.equal(te.a.toolName, 'Read', 'toolEnd resolves name from tool_use_id');
  assert.equal(te.a.isError, true, 'isError propagated');

  stream.close();
  ct.shutdown();
});

// ---------------------------------------------------------------------------
// 3. Assistant-text fallback: growing text across two assistant messages diffed
// ---------------------------------------------------------------------------
await check('3 assistant-text fallback diffing (no duplication)', async () => {
  const stream = makeStream();
  const ct = new ClaudeThread('t3', { queryFn: () => stream.iterable });
  const events = recorder(ct);

  ct.prompt('no stream events please');
  await tick();
  stream.push({ type: 'system', subtype: 'init', session_id: 's3' });
  // No stream_event blocks at all -> fallback path via cumulative assistant text.
  stream.push({ type: 'assistant', message: { content: [{ type: 'text', text: 'Hello' }] } });
  stream.push({ type: 'assistant', message: { content: [{ type: 'text', text: 'Hello, world' }] } });
  stream.push({ type: 'result', subtype: 'success', session_id: 's3' });

  await waitFor(() => events.some((e) => e.name === 'agentEnd'));

  const deltas = events.filter((e) => e.name === 'text').map((e) => e.a);
  const joined = deltas.join('');
  assert.equal(joined, 'Hello, world', `reconstructed text was ${JSON.stringify(joined)} from deltas ${JSON.stringify(deltas)}`);
  // No duplication: the cumulative second message must only contribute the new suffix.
  assert.deepEqual(deltas, ['Hello', ', world'], `expected diffed deltas, got ${JSON.stringify(deltas)}`);

  stream.close();
  ct.shutdown();
});

// ---------------------------------------------------------------------------
// 4 & 5 & 6 & 7 — drive canUseTool from the fake. We need the fake to capture
// options.canUseTool and call it, then resolve based on respondUI.
// ---------------------------------------------------------------------------

// Helper: build a fake whose stream lets us call canUseTool and observe the result.
function approvalHarness(threadId, toolName, input) {
  const stream = makeStream();
  let canUseTool = null;
  let toolPromise = null;
  const queryFn = ({ options }) => {
    canUseTool = options.canUseTool;
    return stream.iterable;
  };
  const ct = new ClaudeThread(threadId, { queryFn });
  const events = recorder(ct);
  // start the loop
  ct.prompt('go');
  return {
    ct, events, stream,
    async invoke() {
      await waitFor(() => canUseTool != null);
      const ac = new AbortController();
      toolPromise = canUseTool(toolName, input, { signal: ac.signal, toolUseID: 'tu' });
      return toolPromise;
    },
    get toolPromise() { return toolPromise; },
  };
}

// 4. Approval ALLOW
await check('4 approval ALLOW (confirm -> behavior allow)', async () => {
  const h = approvalHarness('t4', 'Bash', { command: 'git push origin main' });
  const p = h.invoke();
  await waitFor(() => h.events.some((e) => e.name === 'uiRequest'));
  const ui = h.events.find((e) => e.name === 'uiRequest').a;
  assert.equal(ui.method, 'confirm', `expected confirm, got ${ui.method}`);
  assert.ok(ui.id, 'uiRequest has id');

  h.ct.respondUI(ui.id, { confirmed: true });
  const result = await p;
  assert.equal(result.behavior, 'allow', `expected allow, got ${JSON.stringify(result)}`);
  assert.deepEqual(result.updatedInput, { command: 'git push origin main' });

  h.stream.close();
  h.ct.shutdown();
});

// 5. Approval DENY
await check('5 approval DENY (confirm false -> behavior deny, not run)', async () => {
  const h = approvalHarness('t5', 'Bash', { command: 'git push origin main' });
  const p = h.invoke();
  await waitFor(() => h.events.some((e) => e.name === 'uiRequest'));
  const ui = h.events.find((e) => e.name === 'uiRequest').a;
  assert.equal(ui.method, 'confirm');

  h.ct.respondUI(ui.id, { confirmed: false });
  const result = await p;
  assert.equal(result.behavior, 'deny', `expected deny, got ${JSON.stringify(result)}`);
  assert.ok(!('updatedInput' in result) || result.updatedInput === undefined,
    'deny does not carry updatedInput (command never runs)');

  h.stream.close();
  h.ct.shutdown();
});

// 6. Safe bash auto-allow (no uiRequest)
await check('6 safe bash auto-allow (no uiRequest)', async () => {
  const h = approvalHarness('t6', 'Bash', { command: 'ls -la' });
  const result = await h.invoke();
  assert.equal(result.behavior, 'allow', `expected allow, got ${JSON.stringify(result)}`);
  assert.deepEqual(result.updatedInput, { command: 'ls -la' });
  // give any (erroneous) uiRequest a chance to fire
  await tick(); await tick();
  const ui = h.events.find((e) => e.name === 'uiRequest');
  assert.ok(!ui, 'no uiRequest should be emitted for safe bash');

  h.stream.close();
  h.ct.shutdown();
});

// 7. AskUserQuestion -> select; respond value:'B' -> allow with answers
await check('7 AskUserQuestion select -> updatedInput.answers', async () => {
  const input = { questions: [{ question: 'Q?', header: 'h', options: [{ label: 'A' }, { label: 'B' }] }] };
  const h = approvalHarness('t7', 'AskUserQuestion', input);
  const p = h.invoke();
  await waitFor(() => h.events.some((e) => e.name === 'uiRequest'));
  const ui = h.events.find((e) => e.name === 'uiRequest').a;
  assert.equal(ui.method, 'select', `expected select, got ${ui.method}`);
  assert.deepEqual(ui.options, ['A', 'B'], `expected options [A,B], got ${JSON.stringify(ui.options)}`);

  h.ct.respondUI(ui.id, { value: 'B' });
  const result = await p;
  assert.equal(result.behavior, 'allow', `expected allow, got ${JSON.stringify(result)}`);
  assert.equal(result.updatedInput?.answers?.['Q?'], 'B',
    `expected answers['Q?']==='B', got ${JSON.stringify(result.updatedInput)}`);

  h.stream.close();
  h.ct.shutdown();
});

// ---------------------------------------------------------------------------
// 8. Burst steering: two prompt() calls during one turn -> ONE agentStart/agentEnd
// ---------------------------------------------------------------------------
await check('8 burst steering -> single agentStart/agentEnd pair', async () => {
  const stream = makeStream();
  const ct = new ClaudeThread('t8', { queryFn: () => stream.iterable });
  const events = recorder(ct);

  ct.prompt('first');
  ct.prompt('second'); // quick succession, same turn
  await tick();

  stream.push({ type: 'system', subtype: 'init', session_id: 's8' });
  stream.push({ type: 'stream_event', event: { type: 'content_block_start', content_block: { type: 'text' } } });
  stream.push({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text: 'ok' } } });
  stream.push({ type: 'result', subtype: 'success', session_id: 's8' });

  await waitFor(() => events.some((e) => e.name === 'agentEnd'));
  // allow time for any second (spurious) agentEnd from the debounce window
  await tick(); await tick();

  const starts = events.filter((e) => e.name === 'agentStart').length;
  const ends = events.filter((e) => e.name === 'agentEnd').length;
  assert.equal(starts, 1, `expected 1 agentStart, got ${starts}`);
  assert.equal(ends, 1, `expected 1 agentEnd, got ${ends}`);

  stream.close();
  ct.shutdown();
});

// ---------------------------------------------------------------------------
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
if (failed.length) {
  console.log('FAILURES:');
  for (const f of failed) console.log(`  - ${f.name}: ${f.err}`);
  process.exit(1);
}
process.exit(0);
