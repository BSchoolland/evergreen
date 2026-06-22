import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';
import { PiThread } from './piThread.js';
import { ClaudeThread, generateClaudeThreadName } from './claudeThread.js';

const CONFIG_PATH = path.join(homedir(), '.evergreen', 'config');

/**
 * The interactive engine, read from ~/.evergreen/config (the same one-word file
 * the Python watchdog reads via get_cli()). Defaults to "claude" when unset.
 * "codex" has no interactive bridge, so it falls back to the claude bridge for
 * conversations (the watchdog still runs codex for scheduled skills).
 */
export function getEngine() {
  try {
    return readFileSync(CONFIG_PATH, 'utf8').trim() || 'claude';
  } catch {
    return 'claude';
  }
}

/**
 * Build the right per-thread agent for the configured engine. Both implementations
 * expose the identical surface (prompt/respondUI/abort/shutdown, alive/isStreaming)
 * and emit the same event contract, so interactive.js stays engine-agnostic.
 *
 * @param opts.sessionDir  filesystem session dir (pi)
 * @param opts.sessionId   SDK session id to resume (claude)
 */
export function createAgentThread(engine, threadId, opts = {}) {
  if (engine === 'pi') {
    return new PiThread(threadId, opts);
  }
  // claude (and codex fallback)
  return new ClaudeThread(threadId, opts);
}

/** Engine-aware one-shot thread-title generation. */
export async function generateThreadName(engine, seed, piGenerator, opts = {}) {
  if (engine === 'pi') {
    // pi's generator lives in interactive.js (spawns `pi --mode text`).
    return piGenerator(seed);
  }
  return generateClaudeThreadName(seed, opts);
}
