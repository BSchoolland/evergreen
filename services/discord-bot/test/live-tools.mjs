// Live tool-execution test: drives ClaudeThread against the REAL Agent SDK and
// asserts the tool event contract fires for a harmless read-only shell command.
// Requires working `claude` auth (~/.claude credentials or ANTHROPIC_API_KEY).
//
// Asserts: agentStart fires, at least one toolStart whose toolName is the SDK's
// bash tool (matching toolEnd isError:false), assistant text mentions the output,
// agentEnd fires, a session id is captured, and no cmdError. Also records the
// EXACT tool name(s) the SDK reports so we can compare to interactive.js
// _formatTool's expectations ('bash','read',...).
import { ClaudeThread } from '../claudeThread.js';

const MARKER = 'hello-evergreen';
const events = [];
const toolStarts = [];   // [{toolName, args}]
const toolEnds = [];     // [{toolName, isError}]
const ct = new ClaudeThread('live-tools-test', { thinking: 'low', idleMs: 5 * 60 * 1000 });

let sessionId = null;
let textBuf = '';
for (const ev of ['agentStart', 'textStart', 'textEnd', 'uiRequest', 'cmdError', 'agentEnd', 'idle', 'exit']) {
  ct.on(ev, (a) => events.push(ev + (a && a.error ? `(${a.error})` : '')));
}
ct.on('text', (d) => { textBuf += d; });
ct.on('session', (id) => { sessionId = id; });
ct.on('toolStart', ({ toolName, args }) => { events.push('toolStart'); toolStarts.push({ toolName, args }); });
ct.on('toolEnd', ({ toolName, isError }) => { events.push('toolEnd'); toolEnds.push({ toolName, isError }); });

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
  console.log('Prompting agent to run a read-only shell command…');
  ct.prompt(
    `Run the bash command: echo ${MARKER}\n` +
    `Use the Bash tool to run exactly that command, then tell me what it printed.`,
  );
  await waitFor(() => events.includes('agentEnd'), 180000);

  console.log('events:', events.join(' '));
  console.log('sessionId:', sessionId);
  console.log('toolStarts:', JSON.stringify(toolStarts));
  console.log('toolEnds:', JSON.stringify(toolEnds));
  console.log('text:', JSON.stringify(textBuf.trim().slice(0, 400)));

  // Identify the bash tool by its args carrying a `command` (robust to whatever
  // exact name the SDK reports), then record the real name.
  const bashStart = toolStarts.find((t) => t.args && typeof t.args.command === 'string')
    || toolStarts.find((t) => /bash/i.test(t.toolName || ''));
  const bashName = bashStart?.toolName ?? null;
  const bashEnd = bashName ? toolEnds.find((t) => t.toolName === bashName) : null;

  console.log('REAL bash tool name:', JSON.stringify(bashName));
  console.log('all toolStart names:', JSON.stringify([...new Set(toolStarts.map((t) => t.toolName))]));
  console.log('all toolEnd names:', JSON.stringify([...new Set(toolEnds.map((t) => t.toolName))]));

  const checks = [];
  checks.push(['agentStart fired', events.includes('agentStart')]);
  checks.push(['at least one toolStart', toolStarts.length > 0]);
  checks.push(['bash toolStart identified', !!bashStart]);
  checks.push(['bash toolStart carries command arg', !!bashStart?.args?.command]);
  checks.push(['matching bash toolEnd', !!bashEnd]);
  checks.push(['bash toolEnd isError:false', bashEnd ? bashEnd.isError === false : false]);
  checks.push(['text mentions the output', textBuf.includes(MARKER)]);
  checks.push(['agentEnd fired', events.includes('agentEnd')]);
  checks.push(['session captured', !!sessionId]);
  checks.push(['no cmdError', !events.some((e) => e.startsWith('cmdError'))]);

  ct.shutdown();
  await waitFor(() => !ct.alive, 10000).catch(() => {});

  let ok = true;
  for (const [name, pass] of checks) {
    console.log(`${pass ? 'PASS' : 'FAIL'}: ${name}`);
    if (!pass) ok = false;
  }
  console.log(ok ? '\nLIVE TOOLS: ALL PASS' : '\nLIVE TOOLS: FAILURES');
  process.exit(ok ? 0 : 1);
} catch (err) {
  console.error('LIVE TOOLS ERROR:', err?.message || err);
  ct.shutdown();
  process.exit(2);
}
