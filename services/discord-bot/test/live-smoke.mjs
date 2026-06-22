// Live end-to-end smoke test: drives ClaudeThread against the real Agent SDK.
// Requires working `claude` auth (~/.claude credentials or ANTHROPIC_API_KEY).
// Asserts the real event contract fires: agentStart -> text -> agentEnd, a
// session id is captured, and a follow-up turn (steering) works.
import { ClaudeThread } from '../claudeThread.js';

const events = [];
const ct = new ClaudeThread('live-test', { thinking: 'low', idleMs: 5 * 60 * 1000 });

let sessionId = null;
let textBuf = '';
for (const ev of ['agentStart', 'textStart', 'textEnd', 'toolStart', 'toolEnd', 'uiRequest', 'cmdError', 'agentEnd', 'idle', 'exit']) {
  ct.on(ev, (a) => events.push(ev + (a && a.error ? `(${a.error})` : '')));
}
ct.on('text', (d) => { textBuf += d; });
ct.on('session', (id) => { sessionId = id; });

function waitFor(pred, ms) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const iv = setInterval(() => {
      if (pred()) { clearInterval(iv); resolve(true); }
      else if (Date.now() - t0 > ms) { clearInterval(iv); reject(new Error('timeout')); }
    }, 200);
  });
}

try {
  console.log('Turn 1: prompting…');
  ct.prompt('Reply with exactly the word: PONG. Nothing else.');
  await waitFor(() => events.includes('agentEnd'), 120000);

  console.log('events:', events.join(' '));
  console.log('sessionId:', sessionId);
  console.log('text:', JSON.stringify(textBuf.trim().slice(0, 200)));

  const checks = [];
  checks.push(['agentStart fired', events.includes('agentStart')]);
  checks.push(['got text', textBuf.trim().length > 0]);
  checks.push(['agentEnd fired', events.includes('agentEnd')]);
  checks.push(['session captured', !!sessionId]);
  checks.push(['no cmdError', !events.some((e) => e.startsWith('cmdError'))]);

  // Turn 2: same session, verify it stays alive and answers a follow-up.
  events.length = 0; textBuf = '';
  console.log('Turn 2: follow-up on same session…');
  ct.prompt('Now reply with exactly the word: PING. Nothing else.');
  await waitFor(() => events.includes('agentEnd'), 120000);
  console.log('turn2 events:', events.join(' '));
  console.log('turn2 text:', JSON.stringify(textBuf.trim().slice(0, 200)));
  checks.push(['turn2 produced text', textBuf.trim().length > 0]);

  ct.shutdown();
  await waitFor(() => !ct.alive, 10000).catch(() => {});

  let ok = true;
  for (const [name, pass] of checks) {
    console.log(`${pass ? 'PASS' : 'FAIL'}: ${name}`);
    if (!pass) ok = false;
  }
  console.log(ok ? '\nLIVE SMOKE: ALL PASS' : '\nLIVE SMOKE: FAILURES');
  process.exit(ok ? 0 : 1);
} catch (err) {
  console.error('LIVE SMOKE ERROR:', err?.message || err);
  ct.shutdown();
  process.exit(2);
}
