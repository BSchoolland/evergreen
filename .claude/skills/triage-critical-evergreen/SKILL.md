Handle a critical issue identified during triage. Only use this for situations where production or staging is actively down, or a critical security vulnerability is being actively exploited on TACOS — where no reasonable argument could be made that we should wait.

Process:

1. Investigate thoroughly. Read logs, source code, config — understand the issue fully before acting.
2. Send a Discord message to Ben describing the issue, your diagnosis, and your proposed fix.
3. Wait 30 minutes for a response.
4. If no response and you are confident in the fix: act. You MUST have a rollback plan before making any change. Log every action taken to the `runs` table.
5. If unsure about the fix even after investigation: do not act. Send a follow-up Discord message and wait.

Record all actions, reasoning, and rollback steps in the database. Err on the side of not acting.

Discord: use `python3 scripts/discord_send.py "message" --channel $DISCORD_CHANNEL_ID --timeout 1800` to notify Ben and wait for response.
TACOS repo: /home/ben/Projects/TACOS
