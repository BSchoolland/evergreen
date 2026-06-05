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
      model: getConfig('pi_model') || null,
    });
    this.threads.set(threadId, pt);
    this.render.set(threadId, { channel, stream: null, statusMsg: null, typing: null });
    this._wire(pt, threadId);
    return pt;
  }

  _state(threadId) {
    return this.render.get(threadId);
  }

  async _setStatus(threadId, text) {
    const st = this._state(threadId);
    if (!st) return;
    try {
      if (!text) {
        if (st.statusMsg) { await st.statusMsg.delete().catch(() => {}); st.statusMsg = null; }
      } else if (st.statusMsg) {
        await st.statusMsg.edit(text).catch(() => {});
      } else {
        st.statusMsg = await st.channel.send(text).catch(() => null);
      }
    } catch {}
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
      if (st) st.stream = null;
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
    pt.on('toolStart', ({ toolName }) => {
      this._setStatus(threadId, `🔧 \`${toolName}\`…`);
    });
    pt.on('toolEnd', ({ toolName, isError }) => {
      if (isError) this._setStatus(threadId, `⚠️ \`${toolName}\` errored`);
    });
    pt.on('status', (msg) => this._setStatus(threadId, `⏳ ${msg}`));
    pt.on('uiRequest', (ev) => this._handleUIRequest(threadId, ev));
    pt.on('cmdError', (ev) => {
      this._state(threadId)?.channel.send(`⚠️ ${ev.error || 'command failed'}`).catch(() => {});
    });
    pt.on('agentEnd', async () => {
      const st = this._state(threadId);
      if (st?.stream) { await st.stream.finalize(); st.stream = null; }
      await this._setStatus(threadId, null);
      this._stopTyping(threadId);
      touchConversation(threadId);
    });
    pt.on('idle', () => {
      this._stopTyping(threadId);
      setConversationStatus(threadId, 'idle');
    });
    pt.on('exit', (code) => {
      this._stopTyping(threadId);
      if (code && code !== 0 && code !== null) {
        this._state(threadId)?.channel.send(`⚠️ agent process exited (code ${code}). Send another message to restart.`).catch(() => {});
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
    let thread;
    try {
      thread = await message.startThread({
        name: `evergreen · ${new Date().toISOString().slice(5, 16).replace('T', ' ')}`,
        autoArchiveDuration: 1440,
      });
    } catch (e) {
      await message.reply(`Couldn't start a thread: ${e.message}`).catch(() => {});
      return;
    }
    const sessionDir = path.join(SESSIONS_ROOT, thread.id);
    upsertConversation(thread.id, message.channel.id, thread.id, null, {});
    const pt = this._getOrCreatePiThread(thread.id, thread);
    await thread.send('🌲 Started a conversation. I have evergreen\'s skills and DB; ask me to investigate, triage, or fix things — I\'ll ask before anything risky.').catch(() => {});
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
