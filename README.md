# Evergreen

Autonomous DevOps agent for [TACOS](../TACOS). Runs as a Claude Code (or Codex) session powered almost entirely by skills — minimal code, maximum model judgment.

## What it does

- **Monitors database logs** on a configurable interval (default: every 30 minutes), looking for issues using open-ended judgment rather than hardcoded rules
- **Files PRs** to fix problems it finds, then pings you on Discord for approval
- **Tracks security vulnerabilities** by watching HackerNews, running `npm audit`, and monitoring CVEs relevant to the TACOS stack — configurable frequency
- **Provides interactive dashboards and logs** when you open a session manually
- **Can live-debug staging and production** and apply fixes when given explicit permission

## Architecture

The application is ~90% Claude Code skills and natural language instructions, with a thin Python layer for setup and state management.

| Layer | Purpose |
|---|---|
| `evergreen.py` | Entry point — `python evergreen.py` |
| `src/evergreen/` | Python package: DB initialization, first-time setup (claude vs codex), CLI launcher |
| `schema.sql` | SQLite schema for the agent's memory (findings, security alerts, run history) |
| `.claude/skills/` | The actual application logic — short skill files that tell the model what to do, not how to think |
| `~/.evergreen/` | Runtime state: SQLite database, config (gitignored) |

## Getting started

```
python evergreen.py
```

First run prompts you to choose `claude` or `codex`, then drops you into an interactive session with `/setup-evergreen` auto-sent to walk through configuration.

## Philosophy

Traditional applications encode logic in code. Evergreen encodes intent in natural language skills and trusts the model to exercise judgment. A monitoring skill doesn't list every error pattern to watch for — it says "check the logs for issues" and lets the model figure out what matters. This means the entire "application" is a few hundred lines of Python and a handful of short skill files.
