import { spawn } from 'node:child_process';
import { StringDecoder } from 'node:string_decoder';
import { EventEmitter } from 'node:events';
import { existsSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Repo root: services/discord-bot -> ../../
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../');

/**
 * One persistent `pi --mode rpc` child bound to a single Discord thread.
 *
 * The session file (under the thread's --session-dir) is the conversation's
 * memory, so the child can be evicted when idle and respawned with --continue
 * later without losing context. Drives the documented RPC protocol over stdio
 * (strict JSONL, LF-delimited — we never use readline, per pi's framing note).
 *
 * Emits high-level events consumed by interactive.js:
 *   agentStart, textStart, text(delta), textEnd,
 *   toolStart({toolName,args}), toolEnd({toolName,isError}),
 *   uiRequest(rpcEvent), agentEnd, status(msg), cmdError(rpcEvent),
 *   idle, exit(code)
 */
export class PiThread extends EventEmitter {
  constructor(threadId, { sessionDir, projectPath, model, thinking, idleMs = 30 * 60 * 1000 }) {
    super();
    this.threadId = threadId;
    this.sessionDir = sessionDir;
    this.projectPath = projectPath;
    this.model = model || null;
    this.thinking = thinking || null;
    this.idleMs = idleMs;
    this.child = null;
    this.buffer = '';
    this.decoder = new StringDecoder('utf8');
    this.streaming = false;
    this._idleTimer = null;
    this._expectedExitReason = null;
  }

  get alive() {
    return !!this.child && this.child.exitCode === null;
  }

  get isStreaming() {
    return this.streaming;
  }

  _hasExistingSession() {
    try {
      return existsSync(this.sessionDir) &&
        readdirSync(this.sessionDir).some((f) => f.endsWith('.jsonl'));
    } catch {
      return false;
    }
  }

  ensureStarted() {
    if (this.alive) return;
    const args = ['--mode', 'rpc', '--session-dir', this.sessionDir];
    if (this._hasExistingSession()) args.push('--continue');
    if (this.model) args.push('--model', this.model);
    if (this.thinking) args.push('--thinking', this.thinking);

    this.child = spawn('pi', args, {
      cwd: REPO,
      env: {
        ...process.env,
        EVERGREEN_PROJECT_PATH: this.projectPath || '',
      },
    });
    this.buffer = '';

    this.child.stdout.on('data', (chunk) => this._onData(chunk));
    this.child.stderr.on('data', (d) => {
      const msg = d.toString().trim();
      if (msg) console.error(`[pi ${this.threadId}] ${msg}`);
    });
    this.child.on('exit', (code, signal) => {
      const expectedExitReason = this._expectedExitReason;
      this._expectedExitReason = null;
      this.streaming = false;
      this.child = null;
      this._clearIdle();
      this.emit('exit', code, { signal, expectedExitReason });
    });
    this._resetIdle();
  }

  _onData(chunk) {
    this.buffer += this.decoder.write(chunk);
    while (true) {
      const i = this.buffer.indexOf('\n');
      if (i === -1) break;
      let line = this.buffer.slice(0, i);
      this.buffer = this.buffer.slice(i + 1);
      if (line.endsWith('\r')) line = line.slice(0, -1);
      if (!line.trim()) continue;
      let ev;
      try { ev = JSON.parse(line); } catch { continue; }
      this._handle(ev);
    }
  }

  _handle(ev) {
    this._resetIdle();
    switch (ev.type) {
      case 'agent_start':
        this.streaming = true;
        this.emit('agentStart');
        break;
      case 'agent_end':
        this.streaming = false;
        this.emit('agentEnd');
        break;
      case 'message_update': {
        const a = ev.assistantMessageEvent;
        if (!a) break;
        if (a.type === 'text_start') this.emit('textStart');
        else if (a.type === 'text_delta') this.emit('text', a.delta || '');
        else if (a.type === 'text_end') this.emit('textEnd');
        break;
      }
      case 'tool_execution_start':
        this.emit('toolStart', { toolName: ev.toolName, args: ev.args });
        break;
      case 'tool_execution_end':
        this.emit('toolEnd', { toolName: ev.toolName, isError: !!ev.isError });
        break;
      case 'extension_ui_request':
        this.emit('uiRequest', ev);
        break;
      case 'auto_retry_start':
        this.emit('status', `Transient error, retrying (attempt ${ev.attempt}/${ev.maxAttempts})…`);
        break;
      case 'compaction_start':
        this.emit('status', 'Compacting conversation context…');
        break;
      case 'response':
        if (ev.success === false) this.emit('cmdError', ev);
        break;
      default:
        break;
    }
  }

  _send(obj) {
    this.ensureStarted();
    this.child.stdin.write(JSON.stringify(obj) + '\n');
    this._resetIdle();
  }

  /** Send a user turn. If the agent is mid-run, steer instead of rejecting. */
  prompt(text) {
    this.ensureStarted();
    if (this.streaming) {
      this._send({ type: 'prompt', message: text, streamingBehavior: 'steer' });
    } else {
      this._send({ type: 'prompt', message: text });
    }
  }

  /** Answer a pending extension UI request (approval buttons, etc). */
  respondUI(id, payload) {
    this._send({ type: 'extension_ui_response', id, ...payload });
  }

  abort() {
    if (this.alive) this._send({ type: 'abort' });
  }

  _resetIdle() {
    this._clearIdle();
    this._idleTimer = setTimeout(() => this._evict(), this.idleMs);
  }

  _clearIdle() {
    if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }
  }

  _evict() {
    if (this.alive) {
      this._expectedExitReason = 'idle';
      try { this.child.stdin.end(); } catch {}
      try { this.child.kill('SIGTERM'); } catch {}
    }
    this.emit('idle');
  }

  shutdown() {
    this._clearIdle();
    if (this.alive) {
      this._expectedExitReason = 'shutdown';
      try { this.child.kill('SIGTERM'); } catch {}
    }
  }
}
