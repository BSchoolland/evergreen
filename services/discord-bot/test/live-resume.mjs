// Live session-resume test: drives ClaudeThread against the REAL Agent SDK.
// Turn 1 teaches a secret codeword, captures the SDK session id, then shuts the
// thread down (eviction). Turn 2 constructs a BRAND-NEW ClaudeThread that resumes
// the captured session id and asks for the codeword back. If resume rehydrates the
// conversation, the fresh thread (which never saw the original prompt) replies with
// the codeword. Requires working `claude` auth.
import { ClaudeThread } from '../claudeThread.js';

const CODEWORD = 'GREENHOUSE';

function waitFor(pred, ms, label) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const iv = setInterval(() => {
      if (pred()) { clearInterval(iv); resolve(true); }
      else if (Date.now() - t0 > ms) { clearInterval(iv); reject(new Error(`timeout: ${label}`)); }
    }, 200);
  });
}

function wire(ct, state) {
  for (const ev of ['cmdError', 'agentEnd', 'exit']) {
    ct.on(ev, (a) => state.events.push(ev + (a && a.error ? `(${a.error})` : '')));
  }
  ct.on('text', (d) => { state.text += d; });
  ct.on('session', (id) => { state.session = id; });
  ct.on('exit', () => { state.exited = true; });
}

const checks = [];
let capturedSession = null;

try {
  // ---- Turn 1: teach the codeword on a fresh session ----
  const s1 = { events: [], text: '', session: null, exited: false };
  const ct1 = new ClaudeThread('live-resume-1', { thinking: 'low', idleMs: 5 * 60 * 1000 });
  wire(ct1, s1);

  console.log('Turn 1: teaching codeword on a new session…');
  ct1.prompt(`Remember the secret codeword: ${CODEWORD}. Just acknowledge.`);
  await waitFor(() => s1.events.includes('agentEnd'), 120000, 'turn1 agentEnd');

  capturedSession = s1.session;
  console.log('turn1 events:', s1.events.join(' '));
  console.log('turn1 sessionId:', capturedSession);
  console.log('turn1 text:', JSON.stringify(s1.text.trim().slice(0, 200)));

  checks.push(['turn1 captured session id', !!capturedSession]);
  checks.push(['turn1 no cmdError', !s1.events.some((e) => e.startsWith('cmdError'))]);

  // ---- Evict: shutdown turn-1 thread and wait until it is no longer alive ----
  console.log('Shutting down turn-1 thread…');
  ct1.shutdown();
  await waitFor(() => !ct1.alive, 30000, 'turn1 !alive');
  checks.push(['turn1 thread not alive after shutdown', !ct1.alive]);
  console.log('turn1 alive after shutdown:', ct1.alive);

  if (!capturedSession) throw new Error('no session id captured in turn 1; cannot resume');

  // ---- Turn 2: BRAND NEW thread resuming the captured session ----
  const s2 = { events: [], text: '', session: null, exited: false };
  const ct2 = new ClaudeThread('live-resume-2', {
    sessionId: capturedSession,
    thinking: 'low',
    idleMs: 5 * 60 * 1000,
  });
  wire(ct2, s2);

  console.log('Turn 2: NEW thread resuming session, asking for codeword…');
  ct2.prompt('What was the secret codeword I told you? Reply with only that word.');
  await waitFor(() => s2.events.includes('agentEnd'), 120000, 'turn2 agentEnd');

  console.log('turn2 events:', s2.events.join(' '));
  console.log('turn2 resumed sessionId:', s2.session);
  console.log('turn2 text:', JSON.stringify(s2.text.trim().slice(0, 300)));

  const replyHasCodeword = s2.text.toUpperCase().includes(CODEWORD);
  checks.push(['turn2 produced text', s2.text.trim().length > 0]);
  checks.push(['turn2 no cmdError', !s2.events.some((e) => e.startsWith('cmdError'))]);
  checks.push([`turn2 reply contains "${CODEWORD}" (context survived resume)`, replyHasCodeword]);

  ct2.shutdown();
  await waitFor(() => !ct2.alive, 30000, 'turn2 !alive').catch(() => {});

  let ok = true;
  for (const [name, pass] of checks) {
    console.log(`${pass ? 'PASS' : 'FAIL'}: ${name}`);
    if (!pass) ok = false;
  }
  console.log(ok ? '\nLIVE RESUME: ALL PASS' : '\nLIVE RESUME: FAILURES');
  process.exit(ok ? 0 : 1);
} catch (err) {
  console.error('LIVE RESUME ERROR:', err?.message || err);
  process.exit(2);
}
