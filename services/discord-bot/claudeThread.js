import { EventEmitter } from 'node:events';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { query } from '@anthropic-ai/claude-agent-sdk';

// Repo root: services/discord-bot -> ../../
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../');

const DEFAULT_MODEL = 'claude-opus-4-8';
const DEFAULT_EFFORT = 'high';
const TITLE_MODEL = 'claude-haiku-4-5';

// Outward / irreversible bash that should pause for owner confirmation. Mirrors
// (and hardens) the policy of the old pi approval extension: the watchdog stays
// autonomous, but the interactive dev confirms anything that pushes code, opens/
// merges PRs, deploys, destroys files, escalates privilege, or runs remotely.
// All patterns are case-insensitive; isOutwardBash also lowercases as a backstop.
const OUTWARD_BASH = [
  /\bgit\s+(-c\s+\S+\s+)?push\b/i,
  /\bgit\s+\S*\s*--force(-with-lease)?\b/i,
  /\bgit\s+reset\s+--hard\b/i,
  /\bgh\s+pr\s+(create|merge|close|edit|ready)\b/i,
  /\bgh\s+release\b/i,
  /\bgh\s+api\b[^|]*-x\s*(post|put|delete|patch)/i,
  /\b(npm|yarn|pnpm)\s+publish\b/i,
  /\bdocker\s+push\b/i,
  /\brm\s+-[a-z]*r[a-z]*f?\b/i,
  /\brm\s+-[a-z]*f[a-z]*r?\b/i,
  /\bdeploy\b/i,
  /\bkubectl\s+(apply|delete|create|replace)\b/i,
  /\bterraform\s+(apply|destroy)\b/i,
  /\baws\s+\S+\s+(rm|delete)\b/i,
  /\bcurl\b[^|]*\|\s*(ba)?sh\b/i,
  /\bwget\b[^|]*\|\s*(ba)?sh\b/i,
  /^\s*sudo\b/i,
  /\bssh\s+\S+/i,
  /\bscp\b/i,
  /\bchmod\s+-[a-z]*r[a-z]*\s+777\b/i,
];

// First-class file-mutation tools the SDK exposes (no shell needed). Under pi
// these did not exist as tools — file writes went through bash and were gated
// there — so they must be confined explicitly here.
const WRITE_TOOLS = new Set(['Write', 'Edit', 'MultiEdit', 'NotebookEdit']);

function isOutwardBash(command) {
  if (!command) return false;
  const c = String(command).toLowerCase();
  return OUTWARD_BASH.some((re) => re.test(c));
}

function userMessage(text) {
  return {
    type: 'user',
    message: { role: 'user', content: text },
    parent_tool_use_id: null,
  };
}

function modelSupportsAdaptiveThinking(model) {
  return /opus-4-(7|8)/.test(model || '');
}

/**
 * One persistent Claude Agent SDK session bound to a single Discord thread.
 *
 * Drop-in peer of PiThread: exposes the same surface (ensureStarted/prompt/
 * respondUI/abort/shutdown, `alive`/`isStreaming`) and emits the identical event
 * contract consumed by interactive.js:
 *   agentStart, textStart, text(delta), textEnd,
 *   toolStart({toolName,args}), toolEnd({toolName,isError}),
 *   uiRequest(rpcEvent), agentEnd, status(msg), cmdError(rpcEvent),
 *   session(sessionId), idle, exit(code)
 *
 * Internally it runs `query()` in streaming-input mode: a single long-lived
 * agent loop per thread fed by a pushable async generator, so follow-up user
 * messages "steer" the same conversation. The SDK session id (captured from the
 * init/result message) is the conversation's memory — when the thread goes idle
 * the loop is closed and later resumed with `{ resume: sessionId }`, mirroring
 * pi's evict + --continue.
 */
export class ClaudeThread extends EventEmitter {
  constructor(threadId, { sessionId, projectPath, model, thinking, idleMs = 30 * 60 * 1000, queryFn } = {}) {
    super();
    this.threadId = threadId;
    this.sessionId = sessionId || null;
    this.projectPath = projectPath || '';
    this.model = model || DEFAULT_MODEL;
    // We reuse pi's `thinking` config slot as the effort level (low|medium|high|xhigh|max).
    this.effort = thinking || DEFAULT_EFFORT;
    this.idleMs = idleMs;
    // Test seam: inject a fake query() to drive the bridge deterministically.
    // Production callers never pass this — it falls back to the real SDK query.
    this._query = queryFn || query;

    this.q = null;
    this._running = false;
    this.streaming = false;
    this._expectedExitReason = null;

    this._idleTimer = null;
    this._endTimer = null;

    // streaming-input queue
    this._queue = [];
    this._waiter = null;
    this._inputClosed = false;

    // text/tool stream state
    this._textOpen = false;
    this._curText = '';
    this._sawStreamText = false;
    this._toolNames = new Map();

    // pending approval/select requests: id -> resolver(payload)
    this._pendingUI = new Map();
    this._uiSeq = 0;
  }

  get alive() {
    return this._running;
  }

  get isStreaming() {
    return this.streaming;
  }

  // --- streaming-input generator ---

  _resetQueue() {
    this._queue = [];
    this._waiter = null;
    this._inputClosed = false;
  }

  _pushInput(msg) {
    if (this._inputClosed) return;
    this._queue.push(msg);
    if (this._waiter) {
      const w = this._waiter;
      this._waiter = null;
      w();
    }
  }

  _closeInput() {
    this._inputClosed = true;
    if (this._waiter) {
      const w = this._waiter;
      this._waiter = null;
      w();
    }
  }

  async *_inputGen() {
    while (true) {
      while (this._queue.length) yield this._queue.shift();
      if (this._inputClosed) return;
      await new Promise((res) => { this._waiter = res; });
    }
  }

  _options() {
    const opts = {
      model: this.model,
      cwd: REPO,
      env: { ...process.env, EVERGREEN_PROJECT_PATH: this.projectPath || '' },
      // Load project skills (.claude/skills) + CLAUDE.md, like the watchdog's claude -p.
      settingSources: ['project'],
      // Everything routes through canUseTool; we auto-allow safe work and only
      // pause for outward bash / AskUserQuestion. (No tool is pre-approved.)
      permissionMode: 'default',
      includePartialMessages: true,
      effort: this.effort,
      maxTurns: 1000,
      canUseTool: (toolName, input, extra) => this._canUseTool(toolName, input, extra),
    };
    if (modelSupportsAdaptiveThinking(this.model)) {
      opts.thinking = { type: 'adaptive' };
    }
    if (this.sessionId) opts.resume = this.sessionId;
    return opts;
  }

  ensureStarted() {
    // Start a fresh loop unless a healthy, still-accepting one is already running.
    // (A loop whose input was closed by evict/abort/shutdown is "draining" — a new
    // prompt must spin up a replacement rather than queue onto the dying loop.)
    if (this._running && !this._inputClosed) return;
    this._startLoop();
  }

  _startLoop() {
    this._gen = (this._gen || 0) + 1;
    this._running = true;
    this._expectedExitReason = null;
    this._resetQueue();
    this._textOpen = false;
    this._curText = '';
    this._sawStreamText = false;
    this._toolNames = new Map();
    this._runLoop(this._gen);
    this._resetIdle();
  }

  async _runLoop(gen) {
    let exitCode = 0;
    const myQuery = this._query({ prompt: this._inputGen(), options: this._options() });
    this.q = myQuery;
    try {
      for await (const msg of myQuery) {
        // A superseded loop (its replacement already started) must stop handling
        // immediately — the SDK may flush buffered messages after an interrupt,
        // and handling them here would interleave with the new loop's stream.
        if (this._gen !== gen) break;
        this._resetIdle();
        this._handle(msg);
      }
    } catch (err) {
      exitCode = 1;
      const detail = err?.message || String(err);
      console.error(`[claude ${this.threadId}] ${detail}`);
      if (!this._expectedExitReason && this._gen === gen) this.emit('cmdError', { error: detail });
    } finally {
      // If a newer loop superseded us (a prompt arrived during teardown), stay
      // quiet — that loop now owns the shared state and the exit lifecycle.
      if (this._gen !== gen) return;
      const reason = this._expectedExitReason;
      this._expectedExitReason = null;
      this._running = false;
      // Finalize any in-progress turn so the consumer flushes streamed text and
      // clears the per-turn status. A turn ending without a 'result' (abort /
      // evict / error) would otherwise never fire textEnd/agentEnd.
      clearTimeout(this._endTimer);
      this._closeText();
      if (this.streaming) {
        this.streaming = false;
        this.emit('agentEnd');
      }
      this._clearIdle();
      // Reject any dangling approvals so the SDK side never hangs.
      for (const [, resolve] of this._pendingUI) {
        try { resolve({ confirmed: false }); } catch {}
      }
      this._pendingUI.clear();
      this.q = null;
      this.emit('exit', exitCode, { expectedExitReason: reason });
    }
  }

  // --- message handling ---

  _handle(msg) {
    switch (msg.type) {
      case 'system':
        if (msg.subtype === 'init' && msg.session_id) this._captureSession(msg.session_id);
        else if (msg.subtype === 'compact_boundary') this.emit('status', 'Compacting conversation context…');
        break;
      case 'stream_event':
        this._handleStreamEvent(msg.event);
        break;
      case 'assistant':
        this._handleAssistant(msg.message);
        break;
      case 'user':
        this._handleToolResults(msg.message);
        break;
      case 'tool_result':
        this._handleToolResults({ content: [msg] });
        break;
      case 'result':
        if (msg.session_id) this._captureSession(msg.session_id);
        this._closeText();
        // Reset at the turn boundary so a stream-only turn (no consolidating
        // assistant message) can't leave the flag set and swallow the next turn.
        this._sawStreamText = false;
        // A completed turn means any in-flight abort either took effect (we'd have
        // thrown instead) or was a no-op; clear it so it can't mask a later crash.
        if (this._expectedExitReason === 'abort') this._expectedExitReason = null;
        if (msg.subtype && msg.subtype !== 'success' && msg.subtype !== 'interrupted') {
          this.emit('cmdError', { error: msg.subtype });
        }
        this._scheduleEnd();
        break;
      default:
        break;
    }
  }

  _captureSession(id) {
    if (id && id !== this.sessionId) {
      this.sessionId = id;
      this.emit('session', id);
    }
  }

  // Fine-grained streaming deltas (preferred source for assistant text).
  _handleStreamEvent(event) {
    if (!event) return;
    const t = event.type;
    if (t === 'content_block_start' && event.content_block?.type === 'text') {
      this._openText();
    } else if (t === 'content_block_delta') {
      const d = event.delta;
      if (d?.type === 'text_delta' && d.text) {
        this._sawStreamText = true;
        this._emitText(d.text);
      }
    }
  }

  _handleAssistant(message) {
    const blocks = message?.content;
    if (!Array.isArray(blocks)) return;
    let fullText = '';
    for (const b of blocks) {
      if (b?.type === 'text' || typeof b?.text === 'string') {
        fullText += b.text || '';
      } else if (b?.type === 'tool_use') {
        this._closeText();
        if (b.id && !this._toolNames.has(b.id)) {
          this._toolNames.set(b.id, b.name);
          this.emit('toolStart', { toolName: b.name, args: b.input });
        }
      }
    }
    // If the fine-grained stream already delivered this text, the assistant
    // message is just the consolidation — don't replay it. Otherwise (no partial
    // stream events available) fall back to diffing the cumulative text.
    if (fullText && !this._sawStreamText) this._emitAssistantText(fullText);
    this._sawStreamText = false;
  }

  _handleToolResults(message) {
    const blocks = message?.content;
    if (!Array.isArray(blocks)) return;
    for (const b of blocks) {
      if (b?.type === 'tool_result') {
        const name = this._toolNames.get(b.tool_use_id) || 'tool';
        this.emit('toolEnd', { toolName: name, isError: !!b.is_error });
      }
    }
  }

  // --- text emit helpers (reconstruct pi's textStart/text/textEnd contract) ---

  _openText() {
    if (!this._textOpen) {
      this._textOpen = true;
      this._curText = '';
      this.emit('textStart');
    }
  }

  _emitText(t) {
    if (!t) return;
    this._openText();
    this.emit('text', t);
    this._curText += t;
  }

  _emitAssistantText(full) {
    this._openText();
    if (full.startsWith(this._curText)) {
      const delta = full.slice(this._curText.length);
      if (delta) { this.emit('text', delta); this._curText = full; }
    } else {
      this._closeText();
      this._openText();
      this.emit('text', full);
      this._curText = full;
    }
  }

  _closeText() {
    if (this._textOpen) {
      this._textOpen = false;
      this.emit('textEnd');
    }
    this._curText = '';
  }

  // End-of-turn is debounced so a burst of user messages (steering) reads as one
  // logical turn rather than flapping the typing indicator / status message.
  _scheduleEnd() {
    clearTimeout(this._endTimer);
    this._endTimer = setTimeout(() => {
      this._closeText();
      this.streaming = false;
      this.emit('agentEnd');
    }, 200);
  }

  // --- tool approval / questions ---

  async _canUseTool(toolName, input, extra) {
    const signal = extra?.signal;

    if (toolName === 'AskUserQuestion') {
      return this._askQuestion(input, signal);
    }

    if (toolName === 'Bash' && isOutwardBash(input?.command)) {
      return this._confirm(input?.command, signal, input);
    }

    // First-class file-mutation tools: confine to the working repo/clone; a write
    // that escapes those roots (e.g. ~/.ssh, /etc, a shell rc) pauses for approval.
    if (WRITE_TOOLS.has(toolName) && !this._isConfinedWrite(input)) {
      const target = input?.file_path || input?.path || input?.notebook_path || '(unknown path)';
      return this._confirm(`${toolName} → ${target} (outside the working repo)`, signal, input);
    }

    // Everything else runs autonomously, matching pi's default behavior.
    return { behavior: 'allow', updatedInput: input };
  }

  _writeRoots() {
    return [REPO, this.projectPath].filter(Boolean).map((r) => path.resolve(r));
  }

  _isConfinedWrite(input) {
    const target = input?.file_path || input?.path || input?.notebook_path;
    if (!target) return false; // unknown target -> treat as unconfined (confirm)
    const abs = path.resolve(REPO, target);
    return this._writeRoots().some((root) => abs === root || abs.startsWith(root + path.sep));
  }

  _confirm(displayCommand, signal, realInput) {
    const id = `${this.threadId}-${++this._uiSeq}`; // no colon: interactive.js parses ui:<id>:<idx>
    return new Promise((resolve) => {
      const settle = (payload) => {
        this._pendingUI.delete(id);
        if (payload?.confirmed) resolve({ behavior: 'allow', updatedInput: realInput });
        else resolve({ behavior: 'deny', message: 'Denied by owner.' });
      };
      this._pendingUI.set(id, settle);
      if (signal) signal.addEventListener('abort', () => settle({ confirmed: false }), { once: true });
      this.emit('uiRequest', {
        id,
        method: 'confirm',
        title: 'Approve command?',
        message: '```\n' + String(displayCommand || '').slice(0, 1500) + '\n```',
      });
    });
  }

  _askQuestion(input, signal) {
    const questions = Array.isArray(input?.questions) ? input.questions : [];
    const q0 = questions[0];
    if (!q0) return { behavior: 'allow', updatedInput: input };

    const options = (q0.options || []).map((o) => (typeof o === 'string' ? o : o.label));
    const id = `${this.threadId}-${++this._uiSeq}`; // no colon: interactive.js parses ui:<id>:<idx>
    return new Promise((resolve) => {
      const settle = (payload) => {
        this._pendingUI.delete(id);
        const answers = {};
        answers[q0.question] = payload?.value ?? options[0];
        // Multi-question prompts: only the first is asked interactively; the rest
        // default to their first option (rare path; the owner can correct in chat).
        for (let i = 1; i < questions.length; i++) {
          const qi = questions[i];
          const first = qi.options?.[0];
          answers[qi.question] = typeof first === 'string' ? first : first?.label;
        }
        resolve({ behavior: 'allow', updatedInput: { ...input, answers } });
      };
      this._pendingUI.set(id, settle);
      if (signal) signal.addEventListener('abort', () => settle({ value: options[0] }), { once: true });
      this.emit('uiRequest', {
        id,
        method: 'select',
        title: q0.header || q0.question,
        message: q0.question,
        options,
      });
    });
  }

  /** Answer a pending uiRequest (approval buttons / question select). */
  respondUI(id, payload) {
    const settle = this._pendingUI.get(id);
    if (settle) settle(payload || {});
  }

  // --- public control surface (matches PiThread) ---

  /** Send a user turn. Mid-run, this steers the live streaming-input session. */
  prompt(text) {
    this.ensureStarted();
    clearTimeout(this._endTimer);
    if (!this.streaming) {
      this.streaming = true;
      this.emit('agentStart');
    }
    this._pushInput(userMessage(text));
    this._resetIdle();
  }

  abort() {
    // Mark the cancel as expected so the loop's catch suppresses cmdError and the
    // exit carries reason 'abort' (the real SDK throws on interrupt rather than
    // yielding a clean interrupted result). Only arm it when a turn is actually
    // in flight — otherwise the reason could linger and mask a later real crash.
    if (this._running && this.streaming) this._expectedExitReason = 'abort';
    try { this.q?.interrupt?.(); } catch {}
  }

  _resetIdle() {
    this._clearIdle();
    // Clamp to the 32-bit setTimeout limit; a larger value would overflow and
    // fire near-immediately, evicting the live session by accident.
    const ms = Math.min(this.idleMs, 2147483647);
    this._idleTimer = setTimeout(() => this._evict(), ms);
  }

  _clearIdle() {
    if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }
  }

  _evict() {
    if (this._running) {
      this._expectedExitReason = 'idle';
      this._closeInput();
      try { this.q?.interrupt?.(); } catch {}
    }
    this.emit('idle');
  }

  shutdown() {
    this._clearIdle();
    if (this._running) {
      this._expectedExitReason = 'shutdown';
      this._closeInput();
      try { this.q?.interrupt?.(); } catch {}
    }
  }
}

/** One-shot thread-title generation (peer of pi's `pi --mode text -p`). */
export async function generateClaudeThreadName(seed, { model } = {}) {
  const prompt =
    'Create a concise Discord thread title for this user message.\n\n' +
    'Rules:\n- Return only the title; no quotes, markdown, or explanation.\n' +
    '- Maximum 8 words and 60 characters.\n- Base it on the user\'s actual request.\n\n' +
    `User message:\n${String(seed || '').slice(0, 4000)}`;

  let out = '';
  try {
    for await (const m of query({
      prompt,
      options: {
        model: model || TITLE_MODEL,
        cwd: REPO,
        settingSources: [],
        allowedTools: [],
        maxTurns: 1,
        permissionMode: 'bypassPermissions',
      },
    })) {
      if (m.type === 'assistant') {
        for (const b of (m.message?.content || [])) {
          if (b?.type === 'text' || typeof b?.text === 'string') out += b.text || '';
        }
      } else if (m.type === 'result' && m.subtype === 'success' && m.result) {
        out = m.result;
      }
    }
  } catch (err) {
    console.error('Claude thread title generation failed:', err?.message || err);
  }
  return out.trim();
}
