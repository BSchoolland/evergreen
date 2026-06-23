import Database from 'better-sqlite3';
import { homedir } from 'os';
import path from 'path';

const stateDir = process.env.EVERGREEN_HOME || path.join(homedir(), '.evergreen');
const dbPath = path.join(stateDir, 'evergreen.db');
const db = new Database(dbPath);

db.pragma('journal_mode = WAL');

db.exec(`
  CREATE TABLE IF NOT EXISTS discord_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_message_id TEXT UNIQUE,
    channel_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('outbound', 'inbound')),
    author_id TEXT,
    content TEXT NOT NULL,
    reply_to_message_id TEXT,
    created_at INTEGER NOT NULL DEFAULT (cast(strftime('%s', 'now') as integer)),
    read_at INTEGER
  );
`);

try { db.exec('ALTER TABLE discord_messages ADD COLUMN author_id TEXT'); } catch (e) {}

export function queueOutbound(content, channelId) {
  const stmt = db.prepare(`
    INSERT INTO discord_messages (channel_id, direction, content)
    VALUES (?, 'outbound', ?)
  `);
  return stmt.run(channelId, content).lastInsertRowid;
}

export function getPendingOutbound() {
  return db.prepare(`
    SELECT * FROM discord_messages
    WHERE direction = 'outbound' AND discord_message_id IS NULL
    ORDER BY created_at ASC
  `).all();
}

export function markSent(rowId, discordMessageId) {
  db.prepare(`
    UPDATE discord_messages SET discord_message_id = ? WHERE id = ?
  `).run(discordMessageId, rowId);
}

export function recordInbound(discordMessageId, channelId, authorId, content, replyToMessageId) {
  const stmt = db.prepare(`
    INSERT OR IGNORE INTO discord_messages (discord_message_id, channel_id, direction, author_id, content, reply_to_message_id)
    VALUES (?, ?, 'inbound', ?, ?, ?)
  `);
  return stmt.run(discordMessageId, channelId, authorId, content, replyToMessageId);
}

export function getUnreadReplies(originalDiscordMessageId) {
  return db.prepare(`
    SELECT * FROM discord_messages
    WHERE direction = 'inbound' AND reply_to_message_id = ? AND read_at IS NULL
    ORDER BY created_at ASC
  `).all(originalDiscordMessageId);
}

export function markRead(rowId) {
  db.prepare(`
    UPDATE discord_messages SET read_at = cast(strftime('%s', 'now') as integer) WHERE id = ?
  `).run(rowId);
}

export function getUnreadInbound() {
  return db.prepare(`
    SELECT * FROM discord_messages
    WHERE direction = 'inbound' AND read_at IS NULL
    ORDER BY created_at ASC
  `).all();
}

export function getSentOutboundIds(maxAgeDays = 7) {
  const cutoff = Math.floor(Date.now() / 1000) - maxAgeDays * 86400;
  return db.prepare(`
    SELECT discord_message_id FROM discord_messages
    WHERE direction = 'outbound' AND discord_message_id IS NOT NULL AND created_at > ?
  `).all(cutoff).map(r => r.discord_message_id);
}

export function hasDiscordMessage(discordMessageId) {
  return !!db.prepare(
    'SELECT 1 FROM discord_messages WHERE discord_message_id = ?'
  ).get(discordMessageId);
}

// --- Config (shared configs table) ---

export function getConfig(key, fallback = null) {
  const row = db.prepare('SELECT value FROM configs WHERE key = ?').get(key);
  return row ? row.value : fallback;
}

// --- Conversations (interactive dev: Discord thread <-> pi session) ---

export function getConversation(threadId) {
  return db.prepare('SELECT * FROM conversations WHERE thread_id = ?').get(threadId);
}

export function upsertConversation(threadId, channelId, sessionId, sessionFile, seed = {}) {
  db.prepare(`
    INSERT INTO conversations (thread_id, channel_id, session_id, session_file, status, seed_issue_type, seed_issue_id)
    VALUES (@thread_id, @channel_id, @session_id, @session_file, 'active', @seed_issue_type, @seed_issue_id)
    ON CONFLICT(thread_id) DO UPDATE SET
      status = 'active',
      session_file = excluded.session_file,
      last_activity_at = cast(strftime('%s', 'now') as integer)
  `).run({
    thread_id: threadId,
    channel_id: channelId,
    session_id: sessionId,
    session_file: sessionFile,
    seed_issue_type: seed.type ?? null,
    seed_issue_id: seed.id ?? null,
  });
  return getConversation(threadId);
}

export function setConversationStatus(threadId, status) {
  db.prepare('UPDATE conversations SET status = ? WHERE thread_id = ?').run(status, threadId);
}

export function setConversationSessionFile(threadId, sessionFile) {
  db.prepare('UPDATE conversations SET session_file = ? WHERE thread_id = ?').run(sessionFile, threadId);
}

export function setConversationSessionId(threadId, sessionId) {
  db.prepare('UPDATE conversations SET session_id = ? WHERE thread_id = ?').run(sessionId, threadId);
}

export function touchConversation(threadId) {
  db.prepare(
    "UPDATE conversations SET last_activity_at = cast(strftime('%s', 'now') as integer) WHERE thread_id = ?"
  ).run(threadId);
}

export default db;
