import Database from 'better-sqlite3';
import { homedir } from 'os';
import path from 'path';

const dbPath = path.join(homedir(), '.evergreen', 'evergreen.db');
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
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    read_at TEXT
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
    UPDATE discord_messages SET read_at = datetime('now') WHERE id = ?
  `).run(rowId);
}

export function getUnreadInbound() {
  return db.prepare(`
    SELECT * FROM discord_messages
    WHERE direction = 'inbound' AND read_at IS NULL
    ORDER BY created_at ASC
  `).all();
}

export default db;
