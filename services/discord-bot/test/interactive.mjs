// Deterministic integration test: interactive.js rendering against a mock Discord
// channel, driven by a ClaudeThread with an injected queryFn (no network).
//
// We pre-seed manager.threads with our injected ClaudeThread so the manager's
// _getOrCreateThread returns it (instead of building the configured-engine
// thread, which would spawn a real `pi`/SDK process), then exercise the public
// surface: handleMessage / handleInteraction. The manager wires our thread via
// the real _wire path, so the full event contract -> Discord render is tested.
import { InteractiveManager } from '../interactive.js';
import { ClaudeThread } from '../claudeThread.js';
import { upsertConversation } from '../db.js';

const OWNER = 'owner-123';
const BOT = 'bot-456';
const THREAD_ID = 'test-thread-' + Date.now();

const findings = [];
const results = [];
function check(name, pass, detail = '') {
  results.push({ name, pass, detail });
}

// ---- Fake Discord plumbing -------------------------------------------------

let msgSeq = 0;
function makeMessage(channel, content, components) {
  const id = 'msg-' + (++msgSeq);
  const m = {
    id,
    content: typeof content === 'string' ? content : (content?.content ?? ''),
    components: (typeof content === 'object' ? content?.components : components) || [],
    deleted: false,
    edits: [],
    async edit(next) {
      const text = typeof next === 'string' ? next : next?.content ?? '';
      this.content = text;
      this.edits.push(text);
      channel.calls.push({ op: 'msg.edit', id, text });
      return this;
    },
    async delete() {
      this.deleted = true;
      channel.calls.push({ op: 'msg.delete', id });
      return this;
    },
  };
  return m;
}

function makeChannel(channelId = THREAD_ID) {
  const channel = {
    id: channelId,
    type: 11, // PublicThread
    isThread: () => true,
    calls: [],
    sent: [],     // all messages produced by send()
    async send(content) {
      const m = makeMessage(channel, content);
      channel.sent.push(m);
      channel.calls.push({ op: 'send', id: m.id, text: m.content, hasComponents: m.components.length > 0 });
      return m;
    },
    async sendTyping() {
      channel.calls.push({ op: 'sendTyping' });
    },
  };
  return channel;
}

function makeInteraction(customId, channel) {
  return {
    customId,
    user: { id: OWNER },
    isButton: () => true,
    channel,
    updated: null,
    replied: null,
    async update(payload) { this.updated = payload; channel.calls.push({ op: 'interaction.update', payload }); return this; },
    async reply(payload) { this.replied = payload; channel.calls.push({ op: 'interaction.reply', payload }); return this; },
  };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(pred, ms, label) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (pred()) return true;
    await sleep(20);
  }
  throw new Error('timeout waiting for ' + (label || 'condition'));
}

// ---- Injected fake SDK query() --------------------------------------------
// Each test installs a scenario via `nextScenario`. queryFn returns an async
// iterable of SDK messages; it may also call options.canUseTool to exercise the
// approval gate. We expose hooks so the test can await/observe the agent.

let activeScenario = null;
function queryFn({ prompt, options }) {
  const scenario = activeScenario;
  return (async function* () {
    yield { type: 'system', subtype: 'init', session_id: 'sess-' + THREAD_ID };
    if (scenario) {
      yield* scenario({ prompt, options });
    }
    yield { type: 'result', subtype: 'success', session_id: 'sess-' + THREAD_ID };
    // Mirror the REAL streaming-input SDK: query() does NOT return after a
    // result — it stays open awaiting more user turns. If our fake returned
    // here, the run loop would tear down and clear the debounced agentEnd timer
    // before it fires (a test artifact). Block on the input generator instead;
    // shutdown()/abort() closes it.
    for await (const _ of prompt) { /* await steering; never receives in these tests */ }
  })();
}

// Helpers to build SDK stream messages.
const sysInitDone = []; // unused placeholder
function streamTextStart() {
  return { type: 'stream_event', event: { type: 'content_block_start', content_block: { type: 'text' } } };
}
function streamTextDelta(text) {
  return { type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text } } };
}
function toolUseAssistant(id, name, input) {
  return { type: 'assistant', message: { content: [{ type: 'tool_use', id, name, input }] } };
}
function toolResultUser(toolUseId, isError = false) {
  return { type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: toolUseId, is_error: isError }] } };
}

// ---- Build manager + injected thread ---------------------------------------
// (per-check threads are seeded in setup(); see below)

const client = { user: { id: BOT } };
const manager = new InteractiveManager(client, { ownerId: OWNER });

// Each check gets a fresh thread + channel under a distinct threadId. The fake
// query() stays open after 'result' (like the real streaming-input SDK), so the
// scenario runs once per run loop — a new thread per check keeps them isolated.
let liveThreads = [];
function setup(suffix) {
  const threadId = THREAD_ID + '-' + suffix;
  upsertConversation(threadId, threadId, threadId, null, {});
  const channel = makeChannel(threadId);
  const thread = new ClaudeThread(threadId, { queryFn, idleMs: 5 * 60 * 1000 });
  // Replicate _getOrCreateThread's bookkeeping with our injected thread, then
  // wire it through the real _wire so all rendering goes through production code.
  manager.threads.set(threadId, thread);
  manager.render.set(threadId, { channel, stream: null, statusMsg: null, statusLines: [], statusQueue: null, typing: null });
  manager._wire(thread, threadId);
  liveThreads.push(thread);
  return { threadId, channel, thread };
}

// =================== CHECK 1: streamed text -> channel ======================
async function check1_streamedText() {
  const { channel } = setup('c1');
  activeScenario = async function* () {
    yield streamTextStart();
    for (const part of ['Hello', ', ', 'world', '!']) yield streamTextDelta(part);
  };

  // Public entrypoint: an inbound owner message routed to the conversation.
  await manager.handleMessage({ channel, content: 'say hello', author: { id: OWNER } });

  // Wait for agentEnd to flush + finalize the StreamBuffer.
  await waitFor(() => channel.sent.some((m) => /Hello, world!/.test(m.content)), 5000, 'assistant text delivered');

  const textMsg = channel.sent.find((m) => /Hello, world!/.test(m.content));
  check('1: assistant text delivered to channel', !!textMsg, textMsg ? textMsg.content : '(none)');

  const usedSend = channel.calls.some((c) => c.op === 'send');
  check('1: delivered via channel.send (StreamBuffer)', usedSend);

  // Throttling: 4 deltas should NOT produce 4 separate sends (StreamBuffer
  // coalesces under EDIT_THROTTLE_MS). Expect a small number of send/edit ops.
  const renderOps = channel.calls.filter((c) => c.op === 'send' || c.op === 'msg.edit').length;
  check('1: throttled (fewer render ops than deltas)', renderOps < 4, `renderOps=${renderOps} for 4 deltas`);
  if (renderOps >= 4) findings.push({ severity: 'low', issue: `StreamBuffer not throttling: ${renderOps} render ops for 4 deltas`, location: 'interactive.js StreamBuffer' });

  const typed = channel.calls.some((c) => c.op === 'sendTyping');
  check('1: typing indicator started', typed);
}

// =================== CHECK 2: tool status message lifecycle =================
async function check2_toolStatus() {
  const { channel } = setup('c2');
  let statusMsgIdAtToolTime = null;

  activeScenario = async function* () {
    // A tool call: assistant emits tool_use, then a tool_result comes back.
    yield toolUseAssistant('tool-1', 'Read', { path: '/etc/hosts' });
    // give the manager a tick to render the status message
    await sleep(120);
    yield toolResultUser('tool-1', false);
    await sleep(50);
    yield streamTextStart();
    yield streamTextDelta('done');
  };

  await manager.handleMessage({ channel, content: 'read a file', author: { id: OWNER } });

  // A status message should appear while the tool runs.
  await waitFor(() => channel.sent.some((m) => /🔧/.test(m.content) && !m.deleted), 4000, 'tool status message')
    .catch(() => {});
  const statusMsg = channel.sent.find((m) => /🔧/.test(m.content));
  check('2: tool call produced a status message', !!statusMsg, statusMsg ? statusMsg.content : '(none)');
  if (statusMsg) statusMsgIdAtToolTime = statusMsg.id;

  // After agentEnd, _clearStatus must delete it.
  await waitFor(() => {
    const sm = channel.sent.find((m) => m.id === statusMsgIdAtToolTime);
    return sm && sm.deleted;
  }, 4000, 'status message deleted at agentEnd').catch(() => {});

  const statusAfter = channel.sent.find((m) => m.id === statusMsgIdAtToolTime);
  check('2: status message deleted at agentEnd (_clearStatus)', !!statusAfter && statusAfter.deleted,
    statusAfter ? `deleted=${statusAfter.deleted}` : '(status msg never created)');
}

// =================== CHECK 3: approval uiRequest + button ===================
async function check3_approval() {
  const { channel } = setup('c3');
  let gateResult = null;

  activeScenario = async function* ({ options }) {
    // Drive the approval gate directly: outward bash must pause for confirm.
    // We DENY-or-ALLOW based on the simulated button; here we ALLOW (confirmed:true)
    // but the command is NEVER executed (fake query — nothing runs outward).
    const p = options.canUseTool('Bash', { command: 'git push origin main' }, { signal: undefined, toolUseID: 'tool-9' });
    gateResult = await p;
  };

  await manager.handleMessage({ channel, content: 'push the code', author: { id: OWNER } });

  // The uiRequest should render a message with an ActionRow (components).
  await waitFor(() => channel.sent.some((m) => m.components && m.components.length > 0), 4000, 'approval ActionRow rendered');
  const uiMsg = channel.sent.find((m) => m.components && m.components.length > 0);
  check('3: approval renders a message with components', !!uiMsg);

  // Inspect the ActionRow for Allow/Deny buttons.
  let allowBtn = null, denyBtn = null;
  if (uiMsg) {
    const row = uiMsg.components[0];
    const json = typeof row.toJSON === 'function' ? row.toJSON() : row;
    const comps = json.components || [];
    allowBtn = comps.find((c) => /Allow/i.test(c.label));
    denyBtn = comps.find((c) => /Deny/i.test(c.label));
  }
  check('3: ActionRow has Allow + Deny buttons', !!allowBtn && !!denyBtn,
    allowBtn ? `allow=${allowBtn.custom_id} deny=${denyBtn?.custom_id}` : '(no buttons)');

  // Simulate clicking "Allow" -> customId ui:<id>:0
  const customId = allowBtn ? allowBtn.custom_id : null;
  check('3: Allow button customId matches ui:<id>:0', !!customId && /^ui:.+:0$/.test(customId), String(customId));

  if (customId) {
    // Does interactive.js's customId parser even accept this id? ClaudeThread
    // mints UI ids as `${threadId}:${seq}` (contains a colon), but
    // handleInteraction parses with /^ui:([^:]+):(\d+)$/ — [^:]+ forbids colons.
    const parserRe = /^ui:([^:]+):(\d+)$/;
    const parserMatches = parserRe.test(customId);
    check('3: interactive.js customId parser accepts the button id', parserMatches,
      `regex=${parserRe} customId=${customId}`);
    if (!parserMatches) {
      findings.push({
        severity: 'high',
        issue: "Button clicks on Claude-engine threads never route: ClaudeThread mints uiRequest ids as `${threadId}:${seq}` (contains a ':'), but interactive.js handleInteraction parses button customIds with /^ui:([^:]+):(\\d+)$/ where [^:]+ disallows colons, so the regex never matches. handleInteraction returns silently — the owner's Allow/Deny press is dropped and the approval gate hangs until run teardown denies it. Fix: relax the regex to /^ui:(.+):(\\d+)$/ (greedy on the id) or change ClaudeThread to use a colon-free id.",
        location: 'interactive.js:442 (handleInteraction regex) vs claudeThread.js:370,394 (id minting)',
      });
    }

    const interaction = makeInteraction(customId, channel);
    await manager.handleInteraction(interaction);
    // respondUI(confirmed:true) -> gate resolves to behavior:'allow'.
    await waitFor(() => gateResult !== null, 2500, 'approval gate resolved').catch(() => {});
    check('3: handleInteraction routed confirmed:true to respondUI (gate allowed)',
      gateResult && gateResult.behavior === 'allow', JSON.stringify(gateResult));
    check('3: interaction.update called (button disabled/labeled)', !!interaction.updated,
      interaction.updated ? String(interaction.updated.content).slice(-40)
        : (interaction.replied ? 'replied: ' + JSON.stringify(interaction.replied) : '(silently dropped — no update, no reply)'));
  } else {
    check('3: handleInteraction routed confirmed:true to respondUI (gate allowed)', false, 'no customId');
  }

  await sleep(200);
}

// =================== CHECK 4: pendingInput free-text path ====================
async function check4_pendingInput() {
  const { threadId, channel, thread } = setup('c4');
  let answeredValue = null;

  // interactive.js maps uiRequest method 'input'/'editor' to pendingInput.
  // ClaudeThread emits 'select' for AskUserQuestion, not 'input', so to exercise
  // the pendingInput path deterministically we register a pending resolver on the
  // thread and emit a synthetic input uiRequest through the wired listener, then
  // assert the next handleMessage is consumed as the answer via respondUI.
  const REQ_ID = threadId + ':input-1';
  thread._pendingUI.set(REQ_ID, (payload) => { answeredValue = payload?.value; });

  thread.emit('uiRequest', { id: REQ_ID, method: 'input', title: 'What is your name?', placeholder: 'name' });
  await waitFor(() => manager.pendingInput.get(threadId) === REQ_ID, 2000, 'pendingInput registered');
  check('4: input uiRequest registers pendingInput', manager.pendingInput.get(threadId) === REQ_ID);

  const prompt = channel.sent.find((m) => /✍️/.test(m.content));
  check('4: input prompt message sent to channel', !!prompt, prompt ? prompt.content : '(none)');

  // Next inbound thread message must be consumed as the answer (respondUI), NOT
  // forwarded to the agent as a new prompt.
  await manager.handleMessage({ channel, content: 'Ben', author: { id: OWNER } });
  await waitFor(() => answeredValue !== null, 2000, 'pendingInput answered').catch(() => {});
  check('4: next message consumed as answer via respondUI', answeredValue === 'Ben', `value=${JSON.stringify(answeredValue)}`);
  check('4: pendingInput cleared after answer', !manager.pendingInput.has(threadId));
}

// ---- Run -------------------------------------------------------------------
let exitCode = 0;
try {
  await check1_streamedText();
  await check2_toolStatus();
  await check3_approval();
  await check4_pendingInput();
} catch (err) {
  console.error('TEST ERROR:', err?.stack || err);
  exitCode = 2;
  findings.push({ severity: 'high', issue: 'Test harness threw: ' + (err?.message || err), location: 'test/interactive.mjs' });
} finally {
  for (const t of liveThreads) { try { t.shutdown(); } catch {} }
}

let allPass = true;
for (const r of results) {
  console.log(`${r.pass ? 'PASS' : 'FAIL'}: ${r.name}${r.detail ? '  [' + r.detail + ']' : ''}`);
  if (!r.pass) allPass = false;
}
console.log('\nFINDINGS:', JSON.stringify(findings));
console.log(allPass && exitCode === 0 ? '\nINTERACTIVE INTEGRATION: ALL PASS' : '\nINTERACTIVE INTEGRATION: FAILURES');
process.exit(allPass && exitCode === 0 ? 0 : 1);
