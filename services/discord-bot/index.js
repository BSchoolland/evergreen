import 'dotenv/config';
import { Client, GatewayIntentBits, Events } from 'discord.js';
import { splitMessage } from './messageUtils.js';
import {
  getPendingOutbound,
  markSent,
  recordInbound,
  getSentOutboundIds,
  hasDiscordMessage,
} from './db.js';

const POLL_INTERVAL_MS = 3000;
const REPLY_POLL_INTERVAL_MS = 5 * 60 * 1000;
const CHANNEL_ID = process.env.DISCORD_CHANNEL_ID;
let OWNER_ID = process.env.DISCORD_OWNER_ID || null;

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
  ],
});

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

  startOutboundPoller();
  startReplyPoller();
});

// Handle incoming messages — record replies and mentions
client.on(Events.MessageCreate, async (message) => {
  if (message.author.bot) return;

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
