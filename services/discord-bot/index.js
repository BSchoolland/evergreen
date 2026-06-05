import 'dotenv/config';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Client, GatewayIntentBits, Events, Partials } from 'discord.js';
import { splitMessage } from './messageUtils.js';
import {
  getPendingOutbound,
  markSent,
  recordInbound,
  getSentOutboundIds,
  hasDiscordMessage,
  getConversation,
} from './db.js';
import { InteractiveManager } from './interactive.js';

const POLL_INTERVAL_MS = 3000;
const REPLY_POLL_INTERVAL_MS = 5 * 60 * 1000;
const CHANNEL_ID = process.env.DISCORD_CHANNEL_ID;
let OWNER_ID = process.env.DISCORD_OWNER_ID || null;
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../');

let interactive = null;

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
  ],
  // Threads may arrive uncached; allow partials so thread messages still fire.
  partials: [Partials.Channel],
});

function syncPiSkills() {
  try {
    const p = spawn('python3', ['scripts/sync-pi-skills.py'], { cwd: REPO });
    p.on('exit', (code) => console.log(`pi skill sync exited (${code})`));
  } catch (err) {
    console.error('Failed to sync pi skills:', err.message);
  }
}

client.once(Events.ClientReady, async (c) => {
  console.log(`Logged in as ${c.user.tag}`);

  if (!OWNER_ID) {
    try {
      const channel = await c.channels.fetch(CHANNEL_ID);
      const guild = channel.guild;
      await guild.fetch();
      OWNER_ID = guild.ownerId;
      console.log(`Resolved server owner: ${OWNER_ID}`);
    } catch (err) {
      console.error('Could not resolve server owner:', err.message);
    }
  }

  syncPiSkills();
  interactive = new InteractiveManager(client, { ownerId: OWNER_ID, channelId: CHANNEL_ID });
  console.log('Interactive dev host ready (threads = conversations).');

  startOutboundPoller();
  startReplyPoller();
});

// Route messages: interactive conversations (threads) vs. notification replies (channel).
client.on(Events.MessageCreate, async (message) => {
  if (message.author.bot) return;

  const isOwner = OWNER_ID && message.author.id === OWNER_ID;
  const inThread = typeof message.channel?.isThread === 'function' && message.channel.isThread();

  // 1) Owner speaking inside a conversation thread (or answering an input prompt) → interactive dev.
  if (interactive && isOwner && inThread) {
    if (getConversation(message.channel.id) || interactive.pendingInput.has(message.channel.id)) {
      try { await interactive.handleMessage(message); } catch (err) { console.error('interactive handleMessage:', err); }
      return;
    }
  }

  // 2) Owner @mentions the bot in the main channel (fresh, not a reply) → start a conversation thread.
  if (interactive && isOwner && !inThread && message.mentions.has(client.user) && !message.reference?.messageId) {
    const seed = message.content.replace(/<@!?\d+>/g, '').trim();
    try { await interactive.startConversation(message, seed); } catch (err) { console.error('startConversation:', err); }
    return;
  }

  // 3) Fallback (notification replies / non-owner mentions) → record for the watchdog, as before.
  const isReply = message.reference?.messageId;
  const isMention = message.mentions.has(client.user);
  if (!isReply && !isMention) return;

  recordInbound(
    message.id,
    message.channelId,
    message.author.id,
    message.content,
    message.reference?.messageId || null,
  );

  if (isMention && !isReply) {
    await message.reply("Message received. I'll pass this along to the next evergreen run.");
  }
});

// Approval / select buttons from the interactive dev.
client.on(Events.InteractionCreate, async (interaction) => {
  if (!interactive) return;
  try { await interactive.handleInteraction(interaction); } catch (err) { console.error('handleInteraction:', err); }
});

// Poll the DB for outbound messages queued by skills/scripts
function startOutboundPoller() {
  setInterval(async () => {
    const pending = getPendingOutbound();
    if (pending.length === 0) return;

    const channel = await client.channels.fetch(CHANNEL_ID).catch(() => null);
    if (!channel) {
      console.error(`Could not fetch channel ${CHANNEL_ID}`);
      return;
    }

    for (const msg of pending) {
      try {
        const targetChannel = msg.channel_id !== CHANNEL_ID
          ? await client.channels.fetch(msg.channel_id).catch(() => channel)
          : channel;

        let content = msg.content;
        if (OWNER_ID) {
          content = content.replace(/@Ben/gi, `<@${OWNER_ID}>`);
        }
        const chunks = splitMessage(content);
        let firstMessageId = null;
        for (const chunk of chunks) {
          const sent = await targetChannel.send(chunk);
          if (!firstMessageId) firstMessageId = sent.id;
        }
        markSent(msg.id, firstMessageId);
      } catch (err) {
        console.error(`Failed to send message #${msg.id}:`, err.message);
      }
    }
  }, POLL_INTERVAL_MS);
}

// Poll channel history for replies we may have missed (bot was offline, event dropped, etc.)
function startReplyPoller() {
  const poll = async () => {
    try {
      const channel = await client.channels.fetch(CHANNEL_ID).catch(() => null);
      if (!channel) return;

      const outboundIds = new Set(getSentOutboundIds());
      if (outboundIds.size === 0) return;

      const messages = await channel.messages.fetch({ limit: 100 });
      let recorded = 0;

      for (const msg of messages.values()) {
        if (msg.author.bot) continue;
        if (hasDiscordMessage(msg.id)) continue;

        const replyTo = msg.reference?.messageId;
        const isMention = msg.mentions.has(client.user);
        if (!replyTo && !isMention) continue;

        if (replyTo && outboundIds.has(replyTo)) {
          recordInbound(msg.id, msg.channelId, msg.author.id, msg.content, replyTo);
          recorded++;
        } else if (isMention) {
          recordInbound(msg.id, msg.channelId, msg.author.id, msg.content, null);
          recorded++;
        }
      }

      if (recorded > 0) {
        console.log(`Reply poller: recorded ${recorded} missed message(s)`);
      }
    } catch (err) {
      console.error('Reply poller error:', err.message);
    }
  };

  poll();
  setInterval(poll, REPLY_POLL_INTERVAL_MS);
}

client.login(process.env.DISCORD_TOKEN);
