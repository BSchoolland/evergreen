Verify `new` bugs in the evergreen database (~/.evergreen/evergreen.db). Does NOT apply to security alerts — only the `bugs` table.

Use `/read-config-evergreen` to get the project path, SSH aliases, and project name.

Pick one `new` bug per run. If multiple are queued, pick the highest severity.

## Steps

1. **Read the bug record** — understand what it claims, what evidence was gathered, and what `probable_root_cause` says. This root cause was written by an AI bug-detection system that has been known to jump to conclusions. Your job is to independently verify or disprove it.

2. **Decompose the report's claims** — before designing anything, list every claim the report makes. This includes explicit claims (stated conclusions and evidence) but also *implicit* claims — facts the report assumes without arguing for them. Bug reports bake assumptions into their narrative as background context ("X doesn't support Y", "the API returns Z") that feel like established facts but are actually testable hypotheses. For each claim, note whether it's something the reporter observed directly or something they inferred/assumed. Your experiment must test the assumed claims, not just re-confirm the observed ones.

3. **Design your experiment** — figure out what would prove or disprove the claimed root cause. Prioritize testing the *implicit* assumptions from step 2 — those are the ones most likely to be wrong and least likely to have been checked. Ask yourself: "What are at least two other explanations for the symptoms described?" before committing to an experiment.

4. **Set up the experiment folder** — create `~/.evergreen/verifications/bug-<id>/` for your POC scripts, test files, and findings.

5. **Run the experiment**:
   - **API/integration issues**: make a real call, inspect the raw response. Don't just re-query the DB for the same symptoms check-bugs already found — that proves the symptom exists, not the cause.
   - **Data anomalies**: trace a specific failing record through the code path. Write tests that run against the existing codebase and find the exact point where things go wrong.
   - **Error spikes**: check if the error is still happening. If it stopped, check what changed (deploy, config, upstream fix). Be skeptical about claims like "the DB shows hundreds of errors using my SELECT COUNT(*) query, so obviously the problem is X!" — high counts prove frequency, not causation. Also check recency: hundreds of errors from a week ago may no longer be relevant in a fast-moving project.
   - **Code bugs**: write a minimal reproduction (script, test, curl) that triggers the faulty path.

   Do not assume you lack access to run an experiment without actually trying. Check what credentials, SSH access, API keys, and database connections are available to you before concluding you're blocked.

6. **Write findings** — create a short, direct `.md` file in the verification folder explaining: what you tested, what you observed, and your conclusion.

7. **Update the bug record** — set `verification_notes` to a 3-sentence summary: what you tried, what you observed, whether it matches the claimed root cause. Set `verification_path` to the folder path.

8. **Set status.** `verified` licenses triage to write a code fix, so only use it when the confirmed cause is a code defect you can name at a specific `file:line`. If the proven cause is outside the code with no code fix, mark `unverified` — reproducing the symptom is not a code-level cause.
   - `verified` — reproduced and root cause confirmed. Or: the claimed root cause was wrong, but you found and confirmed the real bug. Set `verified_root_cause` to the confirmed cause (which may differ from `probable_root_cause`); state only what you actually proved — if one example stands in for a class, or part of the cause is still unconfirmed, say so explicitly rather than generalizing it into a clean cause.
   - `unverified` — couldn't reproduce, evidence doesn't support the claimed cause, or the proven cause is an infra transient with no code fix. Triage can notify-only at most.
   - `dismissed` — evidence actively disproves the bug (e.g., the "anomaly" is actually expected behavior, or the issue was already fixed). Record what disproved it in `--disposition-reason` with `--dismissed-by verify`.
   - `blocked` — you genuinely cannot run the experiment because you lack access (after actually trying). Notify the owner via `/discord-evergreen` explaining exactly what access you need. Do not ask for access you already have.

9. **Notify if it's worth interrupting the owner.** When you mark a bug `verified`, send a Discord heads-up (`/discord-evergreen`) with the confirmed root cause and last-occurrence time for things that genuinely warrant attention now — an active outage, ongoing harm such as a runaway loop burning API spend that won't stop on its own (staging included), data loss or corruption, or a serious regression. Lesser verified bugs flow to triage.
