// Evergreen TEAM Discord bot v2 — a dumb router.
//
// One job: map Discord threads to detached `claude -p` sessions.
// All conversational behavior lives in ../../.claude/skills/discord-live/SKILL.md.
//
// Protocol (see README.md):
//   - message in main channel from an owner  -> create thread, mint session UUID, spawn
//   - message in a known thread              -> SIGTERM live agent (= Claude's native
//     interrupt: reaps tool children, writes [Request interrupted] to the transcript),
//     then `claude -p --resume <uuid>` with the message appended. Same path whether
//     the agent died a second ago or a week ago.
//   - agent exit                             -> nothing. No crash detection, no retries.
//     The human's next message is the retry.
//
// Agents are spawned detached (own session, logs to state/logs/) and are NOT owned
// by this process: restarting the bot leaves running agents alone. Liveness is
// pid + /proc cmdline-contains-session-UUID, so pid reuse can't lie to us.
//
// The bot never reads conversation content to make decisions. Its state is
// integers/UUIDs only: {sessionId, pid, lastDeliveredId} per thread.
//
// Side job (relay): drain outbound rows queued in the shared evergreen.db by
// scripts/discord_send.py (watchdog notifications). Claim-before-send; any
// failure is terminal (send_error set, row never re-picked) — re-send loops are
// structurally impossible. Replies to relay messages are recorded as inbound
// rows so discord_send.py --wait-reply keeps working.

import { Client, Events, GatewayIntentBits } from 'discord.js';
import Database from 'better-sqlite3';
import { execFile, spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ---------- config ----------
const env = parseEnvFile(path.join(__dirname, '.env'));
const TOKEN = env.DISCORD_TOKEN;
const CHANNEL_ID = env.DISCORD_CHANNEL_ID;
const OWNER_IDS = (env.DISCORD_OWNER_ID || '').split(',').map((s) => s.trim()).filter(Boolean);
const CLAUDE_BIN = env.CLAUDE_BIN || path.join(os.homedir(), '.local', 'bin', 'claude');
const MODEL = env.DISCORD_AGENT_MODEL || ''; // empty = the user's default model
const PERMISSION_MODE = env.DISCORD_PERMISSION_MODE || 'bypassPermissions';
const AGENT_CWD = path.resolve(__dirname, '..', '..'); // repo root; sessions are cwd-bound — never change this
const EVERGREEN_HOME = process.env.EVERGREEN_HOME || path.join(os.homedir(), '.evergreen-team');
const DB_PATH = path.join(EVERGREEN_HOME, 'evergreen.db');
const PID_FILE = path.join(EVERGREEN_HOME, 'discord-bot.pid'); // discord_send.py checks this
const STATE_PATH = path.join(__dirname, 'state', 'threads.json');
const LOG_DIR = path.join(__dirname, 'state', 'logs');
const DEBOUNCE_MS = 2000; // batch rapid-fire messages into one interrupt+respawn
const SIGTERM_GRACE_MS = 6000;
const RELAY_POLL_MS = 5000;
const RELAY_BACKLOG_MAX_AGE_S = 600; // skip queue rows older than this at boot (no spam dumps)

if (!TOKEN || !CHANNEL_ID) {
  console.error('DISCORD_TOKEN and DISCORD_CHANNEL_ID are required in .env');
  process.exit(1);
}

// ---------- state: threadId -> { sessionId, pid, lastDeliveredId } ----------
fs.mkdirSync(LOG_DIR, { recursive: true });
fs.mkdirSync(EVERGREEN_HOME, { recursive: true });
const state = loadState();

function loadState() {
  try {
    return JSON.parse(fs.readFileSync(STATE_PATH, 'utf8'));
  } catch {
    return { threads: {} };
  }
}

function saveState() {
  const tmp = STATE_PATH + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2));
  fs.renameSync(tmp, STATE_PATH); // atomic — a torn state file is the one corruption we care about
}

function ensureThread(threadId) {
  if (!state.threads[threadId]) {
    state.threads[threadId] = { sessionId: randomUUID(), pid: null, lastDeliveredId: '0' };
    saveState();
  }
  return state.threads[threadId];
}

// ---------- agent lifecycle ----------
function agentAlive(st) {
  if (!st?.pid) return false;
  try {
    process.kill(st.pid, 0);
  } catch {
    return false;
  }
  // pid-reuse guard: the process must actually be this thread's claude
  // (cmdline contains its session UUID via --session-id/--resume)
  try {
    return fs.readFileSync(`/proc/${st.pid}/cmdline`, 'utf8').includes(st.sessionId);
  } catch {
    return false;
  }
}

async function stopAgent(st) {
  if (!agentAlive(st)) return;
  // SIGTERM is claude's native interrupt: it reaps its own tool children,
  // writes [Request interrupted] into the transcript, and exits cleanly.
  try {
    process.kill(st.pid, 'SIGTERM');
  } catch {}
  const deadline = Date.now() + SIGTERM_GRACE_MS;
  while (Date.now() < deadline) {
    if (!agentAlive(st)) return;
    await sleep(200);
  }
  // Stubborn: nuke its whole session. Tool children live in their OWN process
  // groups, so a plain pid/pgid kill would orphan them — session kill won't.
  await new Promise((res) => execFile('pkill', ['-9', '-s', String(st.pid)], () => res()));
  await sleep(300);
}

function sessionFileExists(sessionId) {
  // sessions live under ~/.claude/projects/<cwd-slug>/<uuid>.jsonl; scan all
  // slugs rather than reimplementing the slug rule
  const root = path.join(os.homedir(), '.claude', 'projects');
  try {
    return fs.readdirSync(root).some((d) => fs.existsSync(path.join(root, d, sessionId + '.jsonl')));
  } catch {
    return false;
  }
}

function spawnAgent(threadId, st, messageLines) {
  const isResume = sessionFileExists(st.sessionId);
  const args = ['-p'];
  if (isResume) args.push('--resume', st.sessionId);
  else args.push('--session-id', st.sessionId);
  if (MODEL) args.push('--model', MODEL);
  args.push('--permission-mode', PERMISSION_MODE);
  args.push(`/discord-live thread_id=${threadId} channel_id=${CHANNEL_ID}\n\nNew Discord message(s):\n${messageLines}`);

  const logFd = fs.openSync(path.join(LOG_DIR, threadId + '.log'), 'a');
  fs.writeSync(logFd, `\n--- spawn ${new Date().toISOString()} resume=${isResume} session=${st.sessionId} ---\n`);
  // detached + unref: the agent is NOT owned by the bot. Bot restarts leave it alone.
  const child = spawn(CLAUDE_BIN, args, {
    cwd: AGENT_CWD,
    detached: true,
    stdio: ['ignore', logFd, logFd],
  });
  fs.closeSync(logFd);
  child.unref();
  // Best-effort bookkeeping while we happen to be the parent. Correctness never
  // depends on this firing — orphans are handled by agentAlive() checks.
  child.on('exit', (code) => {
    const cur = state.threads[threadId];
    if (cur && cur.pid === child.pid) {
      cur.pid = null;
      saveState();
    }
    console.log(`agent for thread ${threadId} exited (code=${code})`);
  });
  child.on('error', (err) => console.error(`spawn failed for thread ${threadId}:`, err.message));
  st.pid = child.pid;
  saveState();
  console.log(`spawned agent pid=${child.pid} thread=${threadId} resume=${isResume}`);
}

// ---------- message intake: debounce + per-thread serialization ----------
const queues = new Map(); // threadId -> { msgs: [], timer, lock: Promise }

function enqueue(threadId, msg) {
  let q = queues.get(threadId);
  if (!q) {
    q = { msgs: [], timer: null, lock: Promise.resolve() };
    queues.set(threadId, q);
  }
  const attachments = [...msg.attachments.values()].map((a) => `[attachment] ${a.url}`);
  const author = msg.member?.displayName || msg.author.globalName || msg.author.username;
  q.msgs.push({ id: msg.id, line: `${author} (msg ${msg.id}): ${[msg.content, ...attachments].filter(Boolean).join('\n')}` });
  clearTimeout(q.timer);
  q.timer = setTimeout(() => {
    q.lock = q.lock.then(() => deliver(threadId)).catch((e) => console.error('deliver:', e));
  }, DEBOUNCE_MS);
}

async function deliver(threadId) {
  const q = queues.get(threadId);
  if (!q || q.msgs.length === 0) return;
  const msgs = q.msgs.splice(0);
  const st = ensureThread(threadId);
  await stopAgent(st);
  spawnAgent(threadId, st, msgs.map((m) => m.line).join('\n'));
  st.lastDeliveredId = msgs[msgs.length - 1].id;
  saveState();
}

// ---------- discord ----------
const client = new Client({
  intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessages, GatewayIntentBits.MessageContent],
});

client.on(Events.MessageCreate, (msg) => {
  onMessage(msg).catch((e) => console.error('onMessage:', e));
});

async function onMessage(msg) {
  if (msg.author.bot) return;

  if (msg.channelId === CHANNEL_ID) {
    // A discord-reply to a relay notification answers the watchdog, not the
    // chat agent: record it for discord_send.py --wait-reply and stop there.
    if (recordRelayReply(msg)) return;
    if (!OWNER_IDS.includes(msg.author.id)) return;
    // New conversation: open a thread on the message
    let thread = msg.hasThread ? msg.thread : null;
    if (!thread) {
      const name = (msg.content || '')
        .replace(/<[@#][!&]?\d+>/g, '') // strip raw mention/channel tokens
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 80) || 'chat';
      thread = await msg.startThread({ name });
    }
    ensureThread(thread.id);
    enqueue(thread.id, msg);
    return;
  }

  // Messages in threads under our channel (bot-created or human-created alike)
  const ch = msg.channel;
  if (ch?.isThread?.() && ch.parentId === CHANNEL_ID) {
    if (!OWNER_IDS.includes(msg.author.id)) return; // non-owners are visible in history, but don't drive
    ensureThread(msg.channelId);
    enqueue(msg.channelId, msg);
  }
}

client.once(Events.ClientReady, async () => {
  console.log(`Logged in as ${client.user.tag}`);
  try {
    fs.writeFileSync(PID_FILE, String(process.pid) + '\n');
  } catch (e) {
    console.error('could not write pid file:', e.message);
  }
  await bootSweep();
  startRelay();
});

// Boot sweep: for each known thread with no live agent, deliver any owner
// messages that postdate lastDeliveredId (i.e. arrived while the bot was down).
async function bootSweep() {
  for (const [threadId, st] of Object.entries(state.threads)) {
    if (agentAlive(st)) continue;
    try {
      const ch = await client.channels.fetch(threadId).catch(() => null);
      if (!ch?.isThread?.()) {
        delete state.threads[threadId]; // thread deleted — prune
        saveState();
        continue;
      }
      if (ch.archived) continue;
      const recent = await ch.messages.fetch({ limit: 10 });
      const missed = [...recent.values()]
        .filter((m) => !m.author.bot && OWNER_IDS.includes(m.author.id))
        .filter((m) => BigInt(m.id) > BigInt(st.lastDeliveredId || '0'))
        .sort((a, b) => (BigInt(a.id) < BigInt(b.id) ? -1 : 1));
      for (const m of missed) enqueue(threadId, m);
      if (missed.length) console.log(`boot sweep: ${missed.length} missed message(s) in thread ${threadId}`);
    } catch (e) {
      console.error(`boot sweep ${threadId}:`, e.message);
    }
  }
}

// ---------- relay: drain discord_send.py's queue from the shared DB ----------
let relayDb = null;

function startRelay() {
  let db;
  try {
    db = new Database(DB_PATH);
  } catch (e) {
    console.error('relay disabled (no DB):', e.message);
    return;
  }
  for (const col of ['claimed_at INTEGER', 'send_error TEXT']) {
    try {
      db.exec(`ALTER TABLE discord_messages ADD COLUMN ${col}`);
    } catch {} // already exists
  }
  // Never dump a backlog: anything queued while no bot was running goes stale.
  const stale = db
    .prepare(
      `UPDATE discord_messages SET send_error='skipped: queued while bot was down'
       WHERE direction='outbound' AND discord_message_id IS NULL AND claimed_at IS NULL
         AND send_error IS NULL AND created_at < ?`
    )
    .run(nowEpoch() - RELAY_BACKLOG_MAX_AGE_S);
  if (stale.changes) console.log(`relay: skipped ${stale.changes} stale queued message(s)`);

  const qPending = db.prepare(
    `SELECT * FROM discord_messages
     WHERE direction='outbound' AND discord_message_id IS NULL AND claimed_at IS NULL AND send_error IS NULL
     ORDER BY id ASC LIMIT 10`
  );
  const qClaim = db.prepare(`UPDATE discord_messages SET claimed_at=? WHERE id=? AND claimed_at IS NULL`);
  const qSent = db.prepare(`UPDATE discord_messages SET discord_message_id=? WHERE id=?`);
  const qErr = db.prepare(`UPDATE discord_messages SET send_error=? WHERE id=?`);
  relayDb = db;

  setInterval(async () => {
    for (const row of qPending.all()) {
      // Claim BEFORE sending. A crash mid-send costs at most one lost
      // notification — never a duplicate, never a loop.
      if (qClaim.run(nowEpoch(), row.id).changes !== 1) continue;
      try {
        const ch =
          (await client.channels.fetch(String(row.channel_id)).catch(() => null)) ||
          (await client.channels.fetch(CHANNEL_ID));
        let firstId = null;
        if (row.image_path) {
          const sent = await ch.send({
            content: row.content ? row.content.slice(0, 1900) : undefined,
            files: [row.image_path],
          });
          firstId = sent.id;
        } else {
          for (const chunk of chunks(row.content)) {
            const sent = await ch.send(chunk);
            if (!firstId) firstId = sent.id;
          }
        }
        qSent.run(firstId, row.id);
      } catch (e) {
        // Terminal. The row is never re-picked — the entire re-send bug class dies here.
        qErr.run(String(e?.message || e).slice(0, 300), row.id);
        console.error(`relay: message #${row.id} failed permanently:`, e?.message);
      }
    }
  }, RELAY_POLL_MS);
}

// Record a human discord-reply to a relay-sent message so discord_send.py
// --wait-reply sees it. Returns true if the message was a relay reply.
function recordRelayReply(msg) {
  const ref = msg.reference?.messageId;
  if (!ref || !relayDb) return false;
  const parent = relayDb
    .prepare(`SELECT id FROM discord_messages WHERE discord_message_id=? AND direction='outbound'`)
    .get(ref);
  if (!parent) return false;
  relayDb
    .prepare(
      `INSERT OR IGNORE INTO discord_messages
       (discord_message_id, channel_id, direction, author_id, content, reply_to_message_id)
       VALUES (?, ?, 'inbound', ?, ?, ?)`
    )
    .run(msg.id, msg.channelId, msg.author.id, msg.content, ref);
  return true;
}

// ---------- utils ----------
function parseEnvFile(p) {
  const out = {};
  try {
    for (const line of fs.readFileSync(p, 'utf8').split('\n')) {
      const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
      if (m && !line.trim().startsWith('#')) out[m[1]] = m[2];
    }
  } catch {}
  return out;
}

function chunks(text, max = 1900) {
  const out = [];
  let cur = '';
  for (let line of String(text ?? '').split('\n')) {
    while (line.length > max) {
      if (cur) {
        out.push(cur);
        cur = '';
      }
      out.push(line.slice(0, max));
      line = line.slice(max);
    }
    if ((cur + '\n' + line).length > max) {
      out.push(cur);
      cur = line;
    } else {
      cur = cur ? cur + '\n' + line : line;
    }
  }
  if (cur) out.push(cur);
  return out.length ? out : [''];
}

const nowEpoch = () => Math.floor(Date.now() / 1000);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

client.login(TOKEN);
