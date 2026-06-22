import { spawn } from 'node:child_process';
import { ActionRowBuilder, ButtonBuilder, ButtonStyle, ChannelType } from 'discord.js';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PiThread } from './piThread.js';
import {
  getConfig,
  getConversation,
  upsertConversation,
  setConversationStatus,
  touchConversation,
} from './db.js';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../');
const SESSIONS_ROOT = path.join(REPO, '.pi', 'sessions', 'threads');
const EDIT_THROTTLE_MS = 900;
const MAX_LEN = 1900; // headroom under Discord's 2000-char limit
const THREAD_TITLE_MODEL = 'openai-codex/gpt-5.4-mini';
const THREAD_TITLE_THINKING = 'low';
const THREAD_TITLE_TIMEOUT_MS = 90_000;
const MAX_THREAD_NAME_LEN = 90;

function chunkText(text) {
  const chunks = [];
  let rest = text;
  while (rest.length > MAX_LEN) {
    let cut = rest.lastIndexOf('\n', MAX_LEN);
    if (cut < MAX_LEN * 0.5) cut = MAX_LEN; // no good newline — hard cut
    chunks.push(rest.slice(0, cut));
    rest = rest.slice(cut).replace(/^\n/, '');
  }
  chunks.push(rest);
  return chunks;
}

function trimThreadName(name) {
  const cleaned = String(name || '')
    .replace(/```[\s\S]*?```/g, '')
    .replace(/^[\s"'`*_~#:-]+|[\s"'`*_~#:-]+$/g, '')
    .replace(/^title\s*[:\-]\s*/i, '')
    .replace(/[\r\n]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!cleaned) return 'New request';
  if (cleaned.length <= MAX_THREAD_NAME_LEN) return cleaned;
  const shortened = cleaned.slice(0, MAX_THREAD_NAME_LEN).replace(/\s+\S*$/, '').trim();
  return shortened || cleaned.slice(0, MAX_THREAD_NAME_LEN).trim();
}

function cleanSeedText(seedText) {
  return String(seedText || '')
    .replace(/<@!?\d+>/g, '')     // user/bot mentions
    .replace(/<@&\d+>/g, '')      // role mentions
    .replace(/<#\d+>/g, '')       // channel mentions
    .replace(/<a?:\w+:\d+>/g, '') // custom emoji
    .replace(/\s+/g, ' ')
    .trim();
}

function fallbackThreadName(seedText) {
  return trimThreadName(cleanSeedText(seedText) || 'New request');
}

async function generateThreadName(seedText) {
  const cleanedSeed = cleanSeedText(seedText);
  const fallback = fallbackThreadName(cleanedSeed);
  if (!cleanedSeed) return fallback;

  const prompt = `Create a concise Discord thread title for this user message.\n\nRules:\n- Return only the title; no quotes, markdown, or explanation.\n- Maximum 8 words and 60 characters.\n- Base it on the user's actual request.\n\nUser message:\n${cleanedSeed.slice(0, 4000)}`;

  return new Promise((resolve) => {
    const args = [
      '--no-tools',
      '--no-session',
      '--no-context-files',
      '--no-skills',
      '--no-extensions',
      '--model', THREAD_TITLE_MODEL,
      '--thinking', THREAD_TITLE_THINKING,
      '--mode', 'text',
      '-p', prompt,
    ];
    const child = spawn('pi', args, { cwd: REPO, env: process.env });
    let stdout = '';
    let stderr = '';
    let settled = false;

    const finish = (name, err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (err) console.error(`Thread title generation failed: ${err}`);
      resolve(trimThreadName(name) || fallback);
    };

    const timer = setTimeout(() => {
      try { child.kill('SIGTERM'); } catch {}
      finish(fallback, 'timed out');
    }, THREAD_TITLE_TIMEOUT_MS);

    child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
    child.on('error', (err) => finish(fallback, err.message));
    child.on('exit', (code) => {
      if (code === 0 && stdout.trim()) finish(stdout.trim());
      else finish(fallback, stderr.trim() || `exit code ${code}`);
    });
  });
}

/** Streams growing assistant text into one-or-more Discord messages, throttled. */
class StreamBuffer {
  constructor(channel) {
    this.channel = channel;
    this.full = '';
    this.messages = [];
    this.rendered = [];
    this.timer = null;
    this.flushing = false;
  }

  write(delta) {
    this.full += delta;
    if (!this.timer) this.timer = setTimeout(() => this._flush(), EDIT_THROTTLE_MS);
  }

  async _flush() {
    this.timer = null;
    if (this.flushing) { this.timer = setTimeout(() => this._flush(), EDIT_THROTTLE_MS); return; }
    this.flushing = true;
    try {
      const chunks = chunkText(this.full);
      for (let i = 0; i < chunks.length; i++) {
        const chunk = chunks[i];
        if (!chunk.trim() && i === chunks.length - 1) continue;
        if (this.messages[i]) {
          if (this.rendered[i] !== chunk) {
            try { await this.messages[i].edit(chunk || '…'); this.rendered[i] = chunk; } catch {}
          }
        } else {
          try { this.messages[i] = await this.channel.send(chunk || '…'); this.rendered[i] = chunk; } catch {}
        }
      }
    } finally {
      this.flushing = false;
    }
  }

  async finalize() {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    await this._flush();
  }

  get isEmpty() {
    return this.full.trim().length === 0;
  }
}

export class InteractiveManager {
  constructor(client, { ownerId, channelId }) {
    this.client = client;
    this.ownerId = ownerId;
    this.channelId = channelId;
    this.threads = new Map();     // threadId -> PiThread
    this.render = new Map();      // threadId -> render state
    this.pendingUI = new Map();   // requestId -> { threadId, message, options }
    this.pendingInput = new Map();// threadId -> requestId (next thread msg is the answer)
  }

  _projectPath() {
    return getConfig('project_path_interactive') || getConfig('project_path') || '';
  }

  _getOrCreatePiThread(threadId, channel) {
    let pt = this.threads.get(threadId);
    if (pt) return pt;
    pt = new PiThread(threadId, {
      sessionDir: path.join(SESSIONS_ROOT, threadId),
      projectPath: this._projectPath(),
      model: getConfig('discord_model') || getConfig('pi_model') || null,
      thinking: getConfig('discord_thinking') || null,
    });
    this.threads.set(threadId, pt);
    this.render.set(threadId, { channel, stream: null, statusMsg: null, statusLines: [], statusQueue: null, typing: null });
    this._wire(pt, threadId);
    return pt;
  }

  _state(threadId) {
    return this.render.get(threadId);
  }

  _formatTool(toolName, args) {
    const a = args || {};
    let detail = '';
    switch (toolName) {
      case 'bash': detail = a.command || ''; break;
      case 'read': case 'edit': case 'write': case 'ls': detail = a.path || a.file || ''; break;
      case 'grep': detail = a.pattern || ''; break;
      case 'find': detail = a.pattern || a.glob || a.query || ''; break;
      default: detail = Object.values(a).find((v) => typeof v === 'string') || '';
    }
    detail = String(detail).replace(/\s+/g, ' ').trim();
    if (detail.length > 250) detail = detail.slice(0, 250) + '…';
    return detail ? `🔧 \`${toolName}\` ${detail}` : `🔧 \`${toolName}\``;
  }

  // Append a tool line to the per-turn status message (one message, edited in
  // place, deleted at end of turn). Serialized via a promise chain so rapid tool
  // calls can't race into separate orphaned messages. Keeps the most recent lines
  // and drops the oldest with a leading "…" when it would exceed one message.
  _pushStatus(threadId, line) {
    const st = this._state(threadId);
    if (!st) return;
    st.statusLines.push(line);
    let content = st.statusLines.join('\n');
    if (content.length > 1900) content = '…\n' + content.slice(content.length - 1897);
    st.statusQueue = (st.statusQueue || Promise.resolve()).then(async () => {
      try {
        if (st.statusMsg) await st.statusMsg.edit(content);
        else st.statusMsg = await st.channel.send(content);
      } catch {}
    });
  }

  async _clearStatus(threadId) {
    const st = this._state(threadId);
    if (!st) return;
    st.statusQueue = (st.statusQueue || Promise.resolve()).then(async () => {
      if (st.statusMsg) { try { await st.statusMsg.delete(); } catch {} st.statusMsg = null; }
    });
    await st.statusQueue;
    st.statusLines = [];
  }

  _startTyping(threadId) {
    const st = this._state(threadId);
    if (!st || st.typing) return;
    const tick = () => st.channel.sendTyping().catch(() => {});
    tick();
    st.typing = setInterval(tick, 8000);
  }

  _stopTyping(threadId) {
    const st = this._state(threadId);
    if (st?.typing) { clearInterval(st.typing); st.typing = null; }
  }

  _wire(pt, threadId) {
    pt.on('agentStart', () => {
      const st = this._state(threadId);
      if (st) { st.stream = null; st.statusLines = []; st.statusMsg = null; st.statusQueue = null; }
      this._startTyping(threadId);
    });
    pt.on('textStart', () => {
      const st = this._state(threadId);
      if (st) st.stream = new StreamBuffer(st.channel);
    });
    pt.on('text', (delta) => {
      const st = this._state(threadId);
      if (st?.stream) st.stream.write(delta);
    });
    pt.on('textEnd', async () => {
      const st = this._state(threadId);
      if (st?.stream) { await st.stream.finalize(); st.stream = null; }
    });
    pt.on('toolStart', ({ toolName, args }) => {
      this._pushStatus(threadId, this._formatTool(toolName, args));
    });
    pt.on('toolEnd', ({ toolName, isError }) => {
      if (isError) this._pushStatus(threadId, `⚠️ \`${toolName}\` errored`);
    });
    pt.on('status', (msg) => this._pushStatus(threadId, `⏳ ${msg}`));
    pt.on('uiRequest', (ev) => this._handleUIRequest(threadId, ev));
    pt.on('cmdError', (ev) => {
      this._state(threadId)?.channel.send(`⚠️ ${ev.error || 'command failed'}`).catch(() => {});
    });
    pt.on('agentEnd', async () => {
      const st = this._state(threadId);
      if (st?.stream) { await st.stream.finalize(); st.stream = null; }
      await this._clearStatus(threadId);
      this._stopTyping(threadId);
      touchConversation(threadId);
    });
    pt.on('idle', () => {
      this._stopTyping(threadId);
      setConversationStatus(threadId, 'idle');
    });
    pt.on('exit', (code, info = {}) => {
      this._stopTyping(threadId);
      if (info.expectedExitReason === 'idle') return;
      if (info.expectedExitReason === 'shutdown') return;

      const exitedUnexpectedly = (code !== 0 && code !== null) || !!info.signal;
      if (exitedUnexpectedly) {
        const detail = code !== null && code !== undefined ? `code ${code}` : `signal ${info.signal}`;
        this._state(threadId)?.channel.send(`⚠️ Agent process exited unexpectedly (${detail}). Send another message to restart.`).catch(() => {});
      }
    });
  }

  async _handleUIRequest(threadId, ev) {
    const st = this._state(threadId);
    if (!st) return;
    const method = ev.method;

    if (method === 'confirm' || method === 'select') {
      const row = new ActionRowBuilder();
      let options;
      if (method === 'confirm') {
        row.addComponents(
          new ButtonBuilder().setCustomId(`ui:${ev.id}:0`).setLabel('Allow').setStyle(ButtonStyle.Success),
          new ButtonBuilder().setCustomId(`ui:${ev.id}:1`).setLabel('Deny').setStyle(ButtonStyle.Danger),
        );
      } else {
        options = (ev.options || []).slice(0, 5);
        options.forEach((opt, i) => {
          row.addComponents(
            new ButtonBuilder().setCustomId(`ui:${ev.id}:${i}`).setLabel(String(opt).slice(0, 80)).setStyle(ButtonStyle.Primary),
          );
        });
      }
      const body = [ev.title, ev.message].filter(Boolean).join('\n').slice(0, MAX_LEN) || 'Approve?';
      const msg = await st.channel.send({ content: `🔐 ${body}`, components: [row] }).catch(() => null);
      this.pendingUI.set(ev.id, { threadId, message: msg, options, method });
      return;
    }

    if (method === 'input' || method === 'editor') {
      const body = [ev.title, ev.placeholder].filter(Boolean).join(' — ') || 'Reply with your answer.';
      await st.channel.send(`✍️ ${body}\n_(reply in this thread)_`).catch(() => {});
      this.pendingInput.set(threadId, ev.id);
      return;
    }

    if (method === 'notify') {
      if (ev.notifyType === 'info') return;
      const icon = ev.notifyType === 'error' ? '⚠️' : ev.notifyType === 'warning' ? '⚠️' : 'ℹ️';
      await st.channel.send(`${icon} ${ev.message}`).catch(() => {});
      return;
    }
    // setStatus / setWidget / setTitle / etc. — fire-and-forget, ignored
  }

  /** Route an inbound Discord message (already owner-checked) to its conversation. */
  async handleMessage(message) {
    const threadId = message.channel.id;

    // A pending free-text input request consumes this message as the answer.
    const pendingReqId = this.pendingInput.get(threadId);
    if (pendingReqId) {
      this.pendingInput.delete(threadId);
      const pt = this.threads.get(threadId) || this._getOrCreatePiThread(threadId, message.channel);
      pt.respondUI(pendingReqId, { value: message.content });
      return;
    }

    const conv = getConversation(threadId);
    if (!conv) {
      // unknown thread — only adopt it if it's one of ours (best effort: ignore)
      return;
    }
    const pt = this._getOrCreatePiThread(threadId, message.channel);
    touchConversation(threadId);
    setConversationStatus(threadId, 'active');
    pt.prompt(message.content);
  }

  /** Start a brand-new conversation from a channel message that @mentioned the bot. */
  async startConversation(message, seedText) {
    const titleSeed = seedText || message.content;
    let thread;
    try {
      thread = await message.startThread({
        name: fallbackThreadName(titleSeed),
        autoArchiveDuration: 1440,
      });
      generateThreadName(titleSeed)
        .then((name) => {
          if (name && name !== thread.name) return thread.setName(name, 'AI-generated title from initial user message');
        })
        .catch((err) => console.error('Thread title rename failed:', err.message));
    } catch (e) {
      await message.reply(`Couldn't start a thread: ${e.message}`).catch(() => {});
      return;
    }
    const sessionDir = path.join(SESSIONS_ROOT, thread.id);
    upsertConversation(thread.id, message.channel.id, thread.id, null, {});
    const pt = this._getOrCreatePiThread(thread.id, thread);
    await thread.send('Thread created').catch(() => {});
    if (seedText && seedText.trim()) pt.prompt(seedText.trim());
  }

  /** Handle an approval/select button click. */
  async handleInteraction(interaction) {
    if (!interaction.isButton()) return;
    if (interaction.user.id !== this.ownerId) {
      await interaction.reply({ content: 'Only the owner can approve actions.', ephemeral: true }).catch(() => {});
      return;
    }
    const m = interaction.customId.match(/^ui:([^:]+):(\d+)$/);
    if (!m) return;
    const [, reqId, idxStr] = m;
    const pending = this.pendingUI.get(reqId);
    if (!pending) {
      await interaction.reply({ content: 'This request expired.', ephemeral: true }).catch(() => {});
      return;
    }
    this.pendingUI.delete(reqId);
    const idx = parseInt(idxStr, 10);
    const pt = this.threads.get(pending.threadId);

    let label;
    if (pending.method === 'confirm') {
      const confirmed = idx === 0;
      pt?.respondUI(reqId, { confirmed });
      label = confirmed ? '✅ Allowed' : '🚫 Denied';
    } else {
      const value = pending.options?.[idx];
      pt?.respondUI(reqId, { value });
      label = `✅ ${value}`;
    }
    await interaction.update({ content: `${pending.message?.content ?? ''}\n\n${label}`, components: [] }).catch(() => {});
  }

  isConversationChannel(channel) {
    return channel?.isThread?.() && channel.type === ChannelType.PublicThread || channel?.isThread?.();
  }

  shutdown() {
    for (const pt of this.threads.values()) pt.shutdown();
  }
}
