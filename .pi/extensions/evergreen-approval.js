// Evergreen interactive-dev approval gate.
//
// pi has no built-in tool approval — it runs tools freely. This extension makes
// the *interactive dev* ask the owner (via ctx.ui.confirm, which the Discord host
// renders as Allow/Deny buttons over the RPC extension-UI protocol) before running
// an outward or irreversible shell command. Local, reversible work inside the
// interactive clone (read/edit/write/grep/find/ls, ordinary bash) runs without
// interruption — only commands that leave the sandbox or can't be undone are gated.
//
// IMPORTANT: this same extension dir is also loaded by the WATCHDOG (same cwd =
// evergreen repo). The watchdog must run autonomously with no human to answer a
// prompt, so we no-op unless EVERGREEN_INTERACTIVE=1, which only the Discord host sets.

const RISKY = new RegExp(
  [
    'git\\s+push',
    'git\\s+[^\\n]*--force',
    'gh\\s+pr\\s+(create|merge|close|ready)',
    'gh\\s+release\\s+create',
    'gh\\s+api\\s+[^|]*-X\\s*(POST|PUT|DELETE|PATCH)',
    '(npm|yarn|pnpm)\\s+publish',
    'rm\\s+-[rf]',
    'kubectl\\s+(apply|delete)',
    'terraform\\s+apply',
    'docker\\s+push',
    '\\bdeploy\\b',
  ].join('|'),
  'i',
);

export default function (pi) {
  if (process.env.EVERGREEN_INTERACTIVE !== '1') return;

  pi.on('tool_call', async (event, ctx) => {
    if (event.toolName !== 'bash') return;
    const cmd = String(event.input?.command ?? '');
    if (!RISKY.test(cmd)) return;
    const shown = cmd.length > 500 ? cmd.slice(0, 500) + '…' : cmd;
    const ok = await ctx.ui.confirm('Approve this command?', shown);
    if (!ok) return { block: true, reason: 'Denied by owner in Discord' };
  });
}
