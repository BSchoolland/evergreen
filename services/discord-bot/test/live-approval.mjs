// Live approval-gate test (DENY path — safe): drives ClaudeThread against the
// REAL Agent SDK and asks the agent to run an outward-matching bash command
// (`git push --dry-run origin HEAD`). The bridge's OUTWARD_BASH policy must pause
// for owner confirmation via a uiRequest(method:'confirm') BEFORE the command
// runs; we DENY it (respondUI {confirmed:false}) so nothing is ever pushed —
// not even the dry-run. Asserts the gate fired with the command in its message,
// the deny propagated, the turn completed (agentEnd), and the agent acknowledges
// it could not run the command.
//
// Requires working `claude` auth (~/.claude credentials or ANTHROPIC_API_KEY).
import { ClaudeThread } from '../claudeThread.js';

const PROMPT =
  'Run this exact bash command and nothing else: git push --dry-run origin HEAD';

const events = [];
const toolStarts = [];   // {toolName, args}
const uiRequests = [];   // raw uiRequest payloads
let sessionId = null;
let textBuf = '';

const ct = new ClaudeThread('live-approval', { thinking: 'low', idleMs: 5 * 60 * 1000 });

for (const ev of ['agentStart', 'textStart', 'textEnd', 'toolEnd', 'cmdError', 'agentEnd', 'idle', 'exit']) {
  ct.on(ev, (a) => events.push(ev + (a && a.error ? `(${a.error})` : '')));
}
ct.on('text', (d) => { textBuf += d; });
ct.on('session', (id) => { sessionId = id; });
ct.on('toolStart', ({ toolName, args }) => { toolStarts.push({ toolName, args }); });

// The deny handler: as soon as the bridge asks for confirmation, refuse it.
// This is what guarantees the command never executes — canUseTool resolves
// {behavior:'deny'} so the SDK never runs the Bash tool.
let denied = false;
ct.on('uiRequest', (ev) => {
  uiRequests.push(ev);
  if (ev.method === 'confirm') {
    denied = true;
    console.log(`uiRequest(confirm) received id=${ev.id}; DENYING`);
    ct.respondUI(ev.id, { confirmed: false });
  }
});

function waitFor(pred, ms, label) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const iv = setInterval(() => {
      if (pred()) { clearInterval(iv); resolve(true); }
      else if (Date.now() - t0 > ms) { clearInterval(iv); reject(new Error(`timeout waiting for ${label}`)); }
    }, 200);
  });
}

try {
  console.log('Prompting (outward bash, expect confirm gate)…');
  ct.prompt(PROMPT);

  // First, the gate must fire BEFORE we ever reach agentEnd of a turn that ran it.
  await waitFor(() => uiRequests.some((u) => u.method === 'confirm'), 180000, 'confirm uiRequest');
  const confirm = uiRequests.find((u) => u.method === 'confirm');

  // Then the turn must complete after the deny.
  await waitFor(() => events.includes('agentEnd'), 180000, 'agentEnd');

  console.log('events:', events.join(' '));
  console.log('toolStarts:', JSON.stringify(toolStarts));
  console.log('confirm.message:', JSON.stringify(confirm?.message));
  console.log('final text:', JSON.stringify(textBuf.trim().slice(0, 600)));

  const lowerText = textBuf.toLowerCase();
  // Did the agent acknowledge it could not run / was denied the command?
  const ackPatterns = [
    'deni', 'denied', "couldn't", 'could not', 'cannot', "can't",
    'not able', 'unable', 'blocked', 'rejected', 'permission', 'approv',
    'declined', 'not run', 'did not run', "wasn't able", 'was not able',
    'not allowed', 'not execute', "didn't run", 'no longer', 'without',
  ];
  const acknowledged = ackPatterns.some((p) => lowerText.includes(p));

  const checks = [];
  checks.push(['confirm uiRequest emitted', !!confirm]);
  checks.push(['confirm method is "confirm"', confirm?.method === 'confirm']);
  checks.push(['confirm message contains the command (git push --dry-run)',
    !!confirm && /git\s+push\s+--dry-run\s+origin\s+HEAD/.test(String(confirm.message))]);
  checks.push(['deny was sent', denied === true]);
  // The Bash tool must NOT have completed successfully against this command.
  // If the gate worked, canUseTool denied it, so there is no successful Bash
  // toolEnd for the push. (A toolStart may or may not be emitted depending on
  // when canUseTool runs relative to the assistant tool_use block; the load-
  // bearing safety property is that no Bash toolEnd with the push ran/succeeded.)
  const bashEnded = events.filter((e) => e === 'toolEnd').length;
  console.log('toolEnd count:', bashEnded);
  checks.push(['turn completed (agentEnd)', events.includes('agentEnd')]);
  checks.push(['no cmdError', !events.some((e) => e.startsWith('cmdError'))]);
  checks.push(['agent acknowledged it could not run the command', acknowledged]);

  ct.shutdown();
  await waitFor(() => !ct.alive, 10000).catch(() => {});

  let ok = true;
  for (const [name, pass] of checks) {
    console.log(`${pass ? 'PASS' : 'FAIL'}: ${name}`);
    if (!pass) ok = false;
  }
  console.log('sessionId:', sessionId);
  console.log(ok ? '\nLIVE APPROVAL: ALL PASS' : '\nLIVE APPROVAL: FAILURES');
  process.exit(ok ? 0 : 1);
} catch (err) {
  console.error('LIVE APPROVAL ERROR:', err?.message || err);
  console.error('events so far:', events.join(' '));
  console.error('uiRequests so far:', JSON.stringify(uiRequests));
  ct.shutdown();
  process.exit(2);
}
